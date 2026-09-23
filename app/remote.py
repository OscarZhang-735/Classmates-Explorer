"""Ephemeral remote credentials; only HMAC fingerprints enter the database."""
import asyncio
import hashlib
import hmac
import json
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass

import httpx
from fastapi import HTTPException

from app.db import ACTIVE
from app.github import Cancelled, GitHubClient, GitHubError

identity = ContextVar("credential_identity", default=None)


def bearer(request):
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value or len(value) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise HTTPException(401, "请提供有效的会话凭据")
    return value


@dataclass(repr=False)
class Session:
    credential: str
    expires: float


class RemoteCredentials:
    def __init__(self, settings, store, transport=None):
        self.settings, self.store, self.transport = settings, store, transport
        self.sessions = {}
        self.clients = {}
        self.lock = asyncio.Lock()
        self.upstream_lock = asyncio.Lock()
        self.last_start = 0.0
        self.runner = None
        self.sweeper = None
        self.pending_creations = 0

    def fingerprint(self, token):
        return "remote:" + hmac.new(self.settings.session_secret.get_secret_value().encode(),
                                     token.encode(), hashlib.sha256).hexdigest()

    def check_capacity(self, credential):
        if (len(self.sessions) >= self.settings.max_sessions or
                sum(s.credential == credential for s in self.sessions.values()) >= self.settings.max_sessions_per_credential):
            raise HTTPException(429, "会话数量已达上限", headers={"Retry-After": "180"})

    async def create(self, token):
        credential = self.fingerprint(token)
        async with self.lock:
            await self._expire()
            self.check_capacity(credential)
            if self.pending_creations >= 5:
                raise HTTPException(429, "服务繁忙，请稍后重试", headers={"Retry-After": "30"})
            self.pending_creations += 1
        try:
            # Do not hold the lifecycle lock during network I/O: leases and logout
            # must still work while GitHub is slow or the worker is retrying.
            await self.validate(token, credential)
            async with self.lock:
                await self._expire()
                self.check_capacity(credential)
                if credential not in self.clients:
                    self.clients[credential] = GitHubClient(self.settings, self.store, self.transport,
                                                            token=token, credential_id=credential)
                key = secrets.token_urlsafe(32)
                self.sessions[key] = Session(credential, time.monotonic() + self.settings.session_ttl_seconds)
                return key
        finally:
            self.pending_creations -= 1

    async def validate(self, token, credential):
        candidate = GitHubClient(self.settings, self.store, self.transport,
                                 token=token, credential_id=credential)
        try:
            async with self.upstream_lock:
                # Per-credential task cooldown includes a quota reserve; allow a
                # validation request so users can reconnect and view saved work.
                cooldown = self.store.cache_get("cooldown:global")
                if cooldown and cooldown["until"] > time.time():
                    raise HTTPException(429, "GitHub 暂时限流", headers={"Retry-After": str(max(1, int(cooldown["until"] - time.time())))})
                await asyncio.sleep(max(0, self.settings.github_request_interval - (time.monotonic() - self.last_start)))
                self.last_start = time.monotonic()
                async with asyncio.timeout(self.settings.request_timeout):
                    response = await candidate.client.post("/graphql", json={"query": "query { viewer { id } }"})
            if response.status_code == 401:
                async with self.lock:
                    await self._revoke(credential)
                raise HTTPException(401, "GitHub Token 无效、已过期或权限不足")
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("Invalid upstream response")
            description = json.dumps(body.get("errors") or body.get("message") or "").lower()
            remaining = response.headers.get("x-ratelimit-remaining")
            if (response.status_code == 429 or "rate limit" in description or "rate_limit" in description
                    or (response.status_code == 403 and (remaining == "0" or "retry-after" in response.headers))):
                try:
                    delay = max(1, int(response.headers.get("retry-after", "60")))
                except ValueError:
                    delay = 60
                until = max(time.time() + delay, candidate.reset_time(response.headers, {}) if remaining == "0" else 0)
                cache_key = candidate.cooldown_key if remaining == "0" else "cooldown:global"
                self.store.cache_put(cache_key, {"until": until}, max(1, until - time.time() + 60))
                raise HTTPException(429, "GitHub 暂时限流", headers={"Retry-After": str(max(1, int(until - time.time())))})
            if response.status_code >= 500:
                raise HTTPException(503, "GitHub 暂时不可用")
            if response.status_code != 200 or not ((body.get("data") or {}).get("viewer") or {}).get("id"):
                raise HTTPException(401, "GitHub Token 无效、已过期或权限不足")
        except (httpx.RequestError, TimeoutError, ValueError, AttributeError, TypeError):
            raise HTTPException(503, "GitHub 暂时不可用") from None
        finally:
            await candidate.close()

    async def authenticate(self, key):
        async with self.lock:
            await self._expire()
            session = self.sessions.get(key)
            if not session:
                raise HTTPException(401, "会话已失效，请重新提供 Token")
            return session.credential

    async def heartbeat(self, key):
        async with self.lock:
            await self._expire()
            if key not in self.sessions:
                raise HTTPException(401, "会话已失效，请重新提供 Token")
            self.sessions[key].expires = time.monotonic() + self.settings.session_ttl_seconds

    async def logout(self, key):
        async with self.lock:
            self.sessions.pop(key, None)
            await self._expire()

    async def _expire(self):
        now = time.monotonic()
        self.sessions = {key: value for key, value in self.sessions.items() if value.expires > now}
        live = {s.credential for s in self.sessions.values()}
        for credential in list(self.clients):
            if credential not in live:
                await self._release(credential)

    async def _release(self, credential):
        for task in self.store.tasks(ACTIVE):
            if task["credential_id"] == credential:
                self.store.update(task["id"], status="paused_credentials")
                if self.runner:
                    await self.runner.cancel_current(task["id"])
        client = self.clients.pop(credential, None)
        if client:
            await client.close()

    async def _revoke(self, credential):
        self.sessions = {k: s for k, s in self.sessions.items() if s.credential != credential}
        await self._release(credential)

    def check(self, task_id):
        task = self.store.get(task_id)
        credential = task["credential_id"]
        if credential not in self.clients or not any(s.credential == credential and s.expires > time.monotonic() for s in self.sessions.values()):
            if task["status"] in ACTIVE:
                self.store.update(task_id, status="paused_credentials")
            raise Cancelled
        return self.clients[credential].check(task_id)

    async def pause(self, task_id, seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.check(task_id)
            await asyncio.sleep(min(.25, max(0, until - time.monotonic())))

    async def query(self, task_id, *args, **kwargs):
        async with self.upstream_lock:
            task = self.check(task_id)
            client = self.clients[task["credential_id"]]
            client.last_start = self.last_start
            try:
                return await client.query(task_id, *args, **kwargs)
            except GitHubError as exc:
                if exc.code == "token_invalid":
                    # Sweep later: cancelling the current worker from itself would deadlock.
                    for session in self.sessions.values():
                        if session.credential == task["credential_id"]:
                            session.expires = 0
                raise
            finally:
                self.last_start = client.last_start

    async def sweep(self):
        while True:
            await asyncio.sleep(1)
            async with self.lock:
                await self._expire()

    async def close(self):
        if self.sweeper:
            self.sweeper.cancel()
            try:
                await self.sweeper
            except asyncio.CancelledError:
                pass
        self.sessions.clear()
        for client in self.clients.values():
            await client.close()
        self.clients.clear()
