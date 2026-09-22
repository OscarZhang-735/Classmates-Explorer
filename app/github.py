import asyncio
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from app.config import Settings
from app.db import Store

META = """query($owner:String!,$repo:String!){
 repository(owner:$owner,name:$repo){ id nameWithOwner url isPrivate
 forks(first:1){totalCount} } rateLimit{cost remaining resetAt} }"""
FORKS = """query($owner:String!,$repo:String!,$cursor:String,$count:Int!){
 repository(owner:$owner,name:$repo){ id isPrivate forks(first:$count,after:$cursor,
 orderBy:{field:CREATED_AT,direction:DESC}){
 totalCount pageInfo{hasNextPage endCursor} nodes{
 id nameWithOwner url createdAt pushedAt stargazerCount parent{id}
 owner{__typename id login avatarUrl url
 ... on User{name bio company location websiteUrl createdAt repositories(first:1,privacy:PUBLIC){totalCount}}
 ... on Organization{name description location websiteUrl createdAt repositories(first:1,privacy:PUBLIC){totalCount}}}
 }}} rateLimit{cost remaining resetAt} }"""
CONTRIBUTIONS = """query($ids:[ID!]!,$from:DateTime!,$to:DateTime!){
 nodes(ids:$ids){id ... on User{contributionsCollection(from:$from,to:$to){
 contributionCalendar{totalContributions} totalCommitContributions
 totalIssueContributions totalPullRequestContributions
 totalPullRequestReviewContributions restrictedContributionsCount
 }}} rateLimit{cost remaining resetAt} }"""


class GitHubError(Exception):
    def __init__(self, code: str, message: str, retryable=True):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable

    def public(self):
        return {"code": self.code, "message": self.message}


class BatchTooLarge(GitHubError):
    pass


class Cancelled(Exception):
    pass


class GitHubClient:
    def __init__(self, settings: Settings, store: Store, transport=None):
        self.settings, self.store = settings, store
        self.client = httpx.AsyncClient(
            base_url="https://api.github.com", transport=transport,
            headers={"Authorization": f"Bearer {settings.github_token.get_secret_value()}",
                     "Accept": "application/vnd.github+json", "User-Agent": "Classmates-Explorer/0.1"},
            timeout=httpx.Timeout(settings.request_timeout, connect=settings.connect_timeout),
            follow_redirects=False)
        self.lock = asyncio.Lock()
        self.last_start = 0.0
        self.cooldown_key = f"cooldown:{settings.credential_id}"

    def check(self, task_id):
        task = self.store.get(task_id)
        if task["status"] == "cancelled":
            raise Cancelled
        if not self.settings.github_token.get_secret_value():
            raise GitHubError("token_missing", "请在服务端配置 GITHUB_TOKEN", False)
        if task["credential_id"] != self.settings.credential_id:
            raise GitHubError("credential_changed", "凭据已更换，请创建新任务", False)
        return task

    async def pause(self, task_id, seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.check(task_id)
            await asyncio.sleep(min(0.25, max(0, until - time.monotonic())))

    def cooldown(self, until: float):
        existing = self.store.cache_get(self.cooldown_key)
        until = max(until, existing["until"] if existing else 0)
        self.store.cache_put(self.cooldown_key, {"until": until}, max(1, until - time.time() + 60))

    async def wait_cooldown(self, task_id):
        value = self.store.cache_get(self.cooldown_key)
        if value and value["until"] > time.time():
            self.store.update(task_id, status="waiting_rate_limit", resume_at=value["until"])
            await self.pause(task_id, value["until"] - time.time())
            self.check(task_id)
            self.store.update(task_id, status="running", resume_at=None)

    @staticmethod
    def reset_time(headers, rate):
        try:
            if headers.get("x-ratelimit-reset"):
                return float(headers["x-ratelimit-reset"])
            if rate.get("resetAt"):
                return datetime.fromisoformat(rate["resetAt"].replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            pass
        return time.time() + 60

    async def query(self, task_id: str, query: str, variables: dict, batch=False) -> dict:
        async with self.lock:
            retries = 0
            while True:
                task = self.check(task_id)
                if task["requests"] >= self.settings.max_task_requests or task["points"] >= self.settings.max_task_points:
                    raise GitHubError("budget_exhausted", "已达到本次运行的 GitHub 查询预算")
                await self.wait_cooldown(task_id)
                await self.pause(task_id, max(0, self.settings.github_request_interval - (time.monotonic() - self.last_start)))
                task = self.check(task_id)
                self.store.update(task_id, requests=task["requests"] + 1)
                self.last_start = time.monotonic()
                response = None
                try:
                    async with asyncio.timeout(self.settings.request_timeout):
                        response = await self.client.post("/graphql", json={"query": query, "variables": variables})
                except (httpx.RequestError, TimeoutError):
                    self.check(task_id)
                    if batch:
                        raise BatchTooLarge("upstream_timeout", "GitHub 贡献查询超时") from None
                    if retries >= self.settings.max_retries:
                        raise GitHubError("upstream_unavailable", "GitHub 网络请求失败") from None
                    await self.pause(task_id, 2 ** retries + random.random())
                    retries += 1
                    continue
                try:
                    body = response.json()
                    if not isinstance(body, dict):
                        body = {}
                except ValueError:
                    body = {}
                data = body.get("data") or {}
                errors = body.get("errors") or []
                rate = data.get("rateLimit") or {}
                task = self.store.get(task_id)
                self.store.update(task_id, points=task["points"] + max(0, int(rate.get("cost") or 0)))
                self.check(task_id)
                remaining = response.headers.get("x-ratelimit-remaining", rate.get("remaining"))
                try:
                    remaining = int(remaining) if remaining is not None else None
                except (TypeError, ValueError):
                    remaining = None
                reset = self.reset_time(response.headers, rate)
                if remaining is not None and remaining < self.settings.rate_limit_reserve:
                    self.cooldown(max(time.time() + 1, reset + 1))
                # Never expose raw GitHub errors or credential-bearing transport exceptions.
                error_text = " ".join(str(e.get("message", "")) + " " + str(e.get("type", "")) for e in errors).lower()
                message = str(body.get("message", "")).lower()
                limited = (response.status_code == 429 or "rate_limit" in error_text
                           or "rate limit" in error_text + message or "secondary rate" in error_text + message
                           or (response.status_code == 403 and (remaining == 0 or "retry-after" in response.headers)))
                if limited:
                    streak = self.store.get(task_id)["rate_limit_streak"] + 1
                    self.store.update(task_id, rate_limit_streak=streak)
                    delay = min(3600, 60 * 2 ** (streak - 1))
                    retry_after = response.headers.get("retry-after")
                    if retry_after:
                        try:
                            delay = max(0, float(retry_after))
                        except ValueError:
                            try:
                                delay = max(0, parsedate_to_datetime(retry_after).timestamp() - time.time())
                            except (ValueError, TypeError):
                                pass
                    until = max(time.time() + delay, reset + 1 if remaining == 0 else 0)
                    self.cooldown(until)
                    if streak >= self.settings.max_consecutive_rate_limits:
                        raise GitHubError("rate_limit_exhausted", "GitHub 持续限流，请稍后重试")
                    continue
                self.store.update(task_id, rate_limit_streak=0)
                if response.status_code == 401:
                    raise GitHubError("token_invalid", "GITHUB_TOKEN 无效或已过期", False)
                if response.status_code == 403:
                    raise GitHubError("access_denied", "GitHub 拒绝访问，请检查凭据权限", False)
                resource_error = any(s in error_text for s in ("timeout", "timed out", "resource limit", "maximum", "too large"))
                if batch and (response.status_code in (502, 504) or resource_error):
                    raise BatchTooLarge("batch_resource_limit", "GitHub 贡献查询超时或超出资源限制")
                if response.status_code in (502, 503, 504):
                    if retries < self.settings.max_retries:
                        await self.pause(task_id, 2 ** retries + random.random())
                        retries += 1
                        continue
                    raise GitHubError("upstream_unavailable", "GitHub 暂时不可用")
                if response.status_code >= 400:
                    raise GitHubError("upstream_error", "GitHub 请求被拒绝", False)
                if not data or (errors and not batch):
                    missing = "not_found" in error_text or "could not resolve" in error_text
                    raise GitHubError("not_found" if missing else "graphql_error",
                                      "仓库不存在或不可访问" if missing else "GitHub 返回查询错误", not missing)
                return {"data": data, "errors": errors}

    async def close(self):
        await self.client.aclose()


def collected_at():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
