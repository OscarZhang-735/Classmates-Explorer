import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.db import Store
from app.main import create_app
from tests.test_explorer import FakeGitHub, fork

ORIGIN = "https://example.github.io"
TOKEN_A = "test-user-a-secret"
TOKEN_B = "test-user-b-secret"


def config(tmp_path, **values):
    defaults = dict(app_mode="remote", github_token=SecretStr(""), unlimited_mode=False,
                    session_secret=SecretStr("a-stable-test-secret-with-32-characters"),
                    allowed_origins=[ORIGIN], database_url=f"sqlite:///{tmp_path / 'remote.sqlite3'}",
                    github_request_interval=0, submissions_per_minute=100,
                    session_creations_per_minute=100)
    defaults.update(values)
    return Settings(_env_file=None, **defaults)


class Upstream(FakeGitHub):
    def __init__(self):
        super().__init__(pages=[[fork()]])
        self.pages[0][0]["owner"]["websiteUrl"] = "https://example.com"
        self.authorizations = []
        self.revoked = set()

    def __call__(self, request):
        token = request.headers["authorization"].removeprefix("Bearer ")
        self.authorizations.append(token)
        if token in self.revoked or token == "invalid":
            return httpx.Response(401, json={"message": "Bad credentials"})
        if "viewer" in json.loads(request.content)["query"]:
            return httpx.Response(200, json={"data": {"viewer": {"id": "SAME-ACCOUNT"}}})
        return super().__call__(request)


def login(client, token=TOKEN_A):
    result = client.post("/api/sessions", headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN})
    assert result.status_code == 201, result.text
    assert result.headers["cache-control"] == "no-store"
    return {"Authorization": "Bearer " + result.json()["session_token"], "Origin": ORIGIN}


def submit(client, headers, url="https://github.com/up/repo"):
    result = client.post("/api/tasks", headers=headers, json={"repository_url": url})
    assert result.status_code == 202, result.text
    return result.json()["id"]


def test_isolation_exports_imports_and_no_secrets(tmp_path, caplog):
    upstream = Upstream()
    app = create_app(config(tmp_path), httpx.MockTransport(upstream), start_worker=False)
    with TestClient(app) as client:
        a, b = login(client), login(client, TOKEN_B)
        ta, tb = submit(client, a), submit(client, b)
        assert ta != tb
        assert client.get("/api/tasks").status_code == 401
        assert client.get("/api/tasks", headers={"Authorization": "Bearer bad token"}).status_code == 401
        for path in [f"/api/tasks/{ta}", f"/api/tasks/{ta}/results", f"/api/tasks/{ta}/export"]:
            assert client.get(path, headers=b).status_code == 404
        for action in ["cancel", "retry", "resume"]:
            assert client.post(f"/api/tasks/{ta}/{action}", headers=b).status_code == 404
        assert client.get("/api/tasks", headers=a).json()["total"] == 1
        client.portal.call(app.state.runner.run, ta)
        assert set(upstream.authorizations[-3:]) == {TOKEN_A}
        client.portal.call(app.state.runner.run, tb)
        assert set(upstream.authorizations[-3:]) == {TOKEN_B}
        exported = client.get(f"/api/tasks/{ta}/export", headers=a)
        assert exported.status_code == 200
        csv = client.get(f"/api/tasks/{ta}/export?format=csv", headers=a)
        assert csv.status_code == 200
        assert "content-disposition" in csv.headers["access-control-expose-headers"].lower()
        imported = client.post("/api/imports", headers=a, json=exported.json())
        assert imported.status_code == 201
        imported_id = imported.json()["id"]
        assert client.get(f"/api/tasks/{imported_id}", headers=b).status_code == 404
        assert client.post(f"/api/tasks/{imported_id}/retry", headers=a).status_code == 409
        assert client.post(f"/api/tasks/{imported_id}/resume", headers=a).status_code == 409
        visible = exported.text + csv.text + client.get("/api/tasks", headers=a).text + caplog.text
        for secret in [TOKEN_A, TOKEN_B, a["Authorization"].split()[1], b["Authorization"].split()[1]]:
            assert secret not in visible
            for path in tmp_path.glob("remote.sqlite3*"):
                assert secret.encode() not in path.read_bytes()
        assert "remote:" not in exported.text
        app.state.store.update(ta, completed_at=0)
        assert submit(client, a) != imported_id
        schema = client.get("/openapi.json").json()
        assert schema["paths"]["/api/tasks"]["post"]["security"] == [{"AppSession": []}]
        assert schema["paths"]["/api/sessions"]["post"]["security"] == [{"GitHubToken": []}]


def test_last_session_expiry_resume_budget_and_restart(tmp_path):
    cfg = config(tmp_path)
    upstream = Upstream()
    app = create_app(cfg, httpx.MockTransport(upstream), start_worker=False)
    with TestClient(app) as client:
        a, sibling = login(client), login(client)
        task = submit(client, a)
        app.state.store.update(task, requests=21, points=42, cursor="checkpoint")
        credential = app.state.store.get(task)["credential_id"]
        app.state.store.cache_put(f"cooldown:{credential}", {"until": time.time()+120}, 180)
        assert client.post("/api/session/logout", headers=a).status_code == 200
        assert app.state.store.get(task)["status"] == "queued"
        async def expire():
            for session in app.state.credentials.sessions.values():
                session.expires = 0
        client.portal.call(expire)
        assert client.post("/api/session/heartbeat", headers=sibling).status_code == 401
        assert not app.state.credentials.clients
        assert app.state.store.get(task)["status"] == "paused_credentials"
        fresh = login(client)
        assert client.post(f"/api/tasks/{task}/resume", headers=fresh).status_code == 202
        preserved = app.state.store.get(task)
        assert (preserved["requests"], preserved["points"], preserved["cursor"]) == (21, 42, "checkpoint")
        assert app.state.store.cache_get(f"cooldown:{credential}")
    restarted = create_app(cfg, httpx.MockTransport(upstream))
    with TestClient(restarted) as client:
        assert restarted.state.store.get(task)["status"] == "paused_credentials"
        assert client.get(f"/api/tasks/{task}", headers=fresh).status_code == 401
        fresh = login(client)
        assert client.get(f"/api/tasks/{task}", headers=fresh).status_code == 200
        assert restarted.state.store.get(task)["status"] == "paused_credentials"


def test_cors_and_removed_local_routes(tmp_path):
    app = create_app(config(tmp_path), httpx.MockTransport(Upstream()), start_worker=False)
    with TestClient(app) as client:
        options = client.options("/api/tasks", headers={"Origin": ORIGIN,
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Authorization,Content-Type"})
        assert options.status_code == 200
        assert options.headers["access-control-allow-origin"] == ORIGIN
        unauthorized = client.get("/api/tasks", headers={"Origin": ORIGIN})
        assert unauthorized.status_code == 401
        assert unauthorized.headers["access-control-allow-origin"] == ORIGIN
        assert client.get("/api/config", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.options("/api/tasks", headers={"Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST"}).status_code == 400
        for path in ["/api/config/github", "/api/config/github/reveal", "/api/config/github/unlimited"]:
            assert client.post(path).status_code == 404
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/api/config").json()["configured"] is False
        assert "{{" not in client.get("/").text


@pytest.mark.parametrize("override", [dict(github_token=SecretStr("forbidden")), dict(unlimited_mode=True),
    dict(session_secret=SecretStr("short")), dict(allowed_origins=["*"]), dict(allowed_origins=[ORIGIN+"/repo"])])
def test_remote_rejects_unsafe_configuration(tmp_path, override):
    with pytest.raises(RuntimeError), TestClient(create_app(config(tmp_path, **override))):
        pass


def test_refuses_local_database(tmp_path):
    cfg = config(tmp_path)
    store = Store(cfg.database_url)
    store.create("https://github.com/up/repo", "old-local-fingerprint", 1000)
    store.engine.dispose()
    with pytest.raises(RuntimeError, match="separate database"), TestClient(create_app(cfg)):
        pass


def test_invalid_and_revoked_token(tmp_path):
    upstream = Upstream()
    app = create_app(config(tmp_path), httpx.MockTransport(upstream), start_worker=False)
    with TestClient(app) as client:
        assert client.post("/api/sessions", headers={"Authorization": "Bearer invalid"}).status_code == 401
        a = login(client)
        task = submit(client, a)
        upstream.revoked.add(TOKEN_A)
        client.portal.call(app.state.runner.run, task)
        assert app.state.store.get(task)["error"]["code"] == "token_invalid"
        assert client.get(f"/api/tasks/{task}", headers=a).status_code == 401
        assert not app.state.credentials.clients


def test_limits_and_resume_conflicts(tmp_path):
    app = create_app(config(tmp_path, max_queued_tasks=1), httpx.MockTransport(Upstream()), start_worker=False)
    with TestClient(app) as client:
        a, b = login(client), login(client, TOKEN_B)
        first = submit(client, a)
        assert submit(client, a) == first
        for who in [a, b]:
            assert client.post("/api/tasks", headers=who, json={"repository_url": "https://github.com/up/other"}).status_code == 429
        client.post("/api/session/logout", headers=a)
        a = login(client)
        submit(client, a, "https://github.com/up/other")
        assert client.post(f"/api/tasks/{first}/resume", headers=a).status_code == 429


def test_session_rate_limit_and_timeout(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout("private transport detail", request=request)
    app = create_app(config(tmp_path, session_creations_per_minute=1), httpx.MockTransport(timeout), start_worker=False)
    with TestClient(app) as client:
        failed = client.post("/api/sessions", headers={"Authorization": f"Bearer {TOKEN_A}"})
        assert failed.status_code == 503
        assert "private transport detail" not in failed.text and TOKEN_A not in failed.text
        assert not app.state.credentials.clients
        limited = client.post("/api/sessions", headers={"Authorization": f"Bearer {TOKEN_A}"})
        assert limited.status_code == 429 and "retry-after" in limited.headers


def test_cooldown_releases_worker_and_preserves_budget(tmp_path):
    app = create_app(config(tmp_path), httpx.MockTransport(Upstream()), start_worker=False)
    with TestClient(app) as client:
        a, b = login(client), login(client, TOKEN_B)
        ta, tb = submit(client, a), submit(client, b)
        cred = app.state.store.get(ta)["credential_id"]
        app.state.store.cache_put(f"cooldown:{cred}", {"until": time.time()+100}, 200)
        client.portal.call(app.state.runner.run, ta)
        assert app.state.store.get(ta)["status"] == "waiting_rate_limit"
        assert app.state.store.get(ta)["requests"] == 0
        client.portal.call(app.state.runner.run, tb)
        assert app.state.store.get(tb)["status"] == "completed"
        app.state.store.cache_put("cooldown:global", {"until": time.time()+100}, 200)
        # All credentials honor the shared-IP cooldown before their next GitHub request.
        app.state.store.update(tb, status="queued", repository_id=None)
        client.portal.call(app.state.runner.run, tb)
        assert app.state.store.get(tb)["status"] == "waiting_rate_limit"
import asyncio
from app.remote import RemoteCredentials


async def test_slow_session_validation_does_not_block_logout(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    upstream = Upstream()
    async def transport(request):
        if request.headers["authorization"] == f"Bearer {TOKEN_B}":
            entered.set()
            await release.wait()
        return upstream(request)
    cfg = config(tmp_path)
    store = Store(cfg.database_url)
    credentials = RemoteCredentials(cfg, store, httpx.MockTransport(transport))
    try:
        first = await credentials.create(TOKEN_A)
        task = store.create("https://github.com/up/repo", credentials.fingerprint(TOKEN_A), 1000)
        pending = asyncio.create_task(credentials.create(TOKEN_B))
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(credentials.logout(first), 1)
        assert store.get(task["id"])["status"] == "paused_credentials"
        release.set()
        await pending
    finally:
        await credentials.close()
        store.engine.dispose()


async def test_logout_cancels_inflight_upstream(tmp_path):
    entered, cancelled = asyncio.Event(), asyncio.Event()
    upstream = Upstream()
    async def transport(request):
        if "viewer" in json.loads(request.content)["query"]:
            return upstream(request)
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
    app = create_app(config(tmp_path), httpx.MockTransport(transport))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            result = await client.post("/api/sessions", headers={"Authorization": f"Bearer {TOKEN_A}"})
            headers = {"Authorization": "Bearer " + result.json()["session_token"]}
            result = await client.post("/api/tasks", headers=headers, json={"repository_url": "https://github.com/up/repo"})
            task = result.json()["id"]
            await asyncio.wait_for(entered.wait(), 2)
            await client.post("/api/session/logout", headers=headers)
            await asyncio.wait_for(cancelled.wait(), 2)
            assert app.state.store.get(task)["status"] == "paused_credentials"
            assert not app.state.credentials.clients


@pytest.mark.parametrize("status,body,headers", [
    (429, {"message": "rate limit"}, {"Retry-After": "60"}),
    (403, {"message": "secondary rate limit"}, {"Retry-After": "60"}),
    (200, {"errors": [{"type": "RATE_LIMITED", "message": "rate limit"}]}, {})])
def test_session_upstream_rate_limits(tmp_path, status, body, headers):
    app = create_app(config(tmp_path), httpx.MockTransport(lambda _: httpx.Response(status, json=body, headers=headers)), start_worker=False)
    with TestClient(app) as client:
        result = client.post("/api/sessions", headers={"Authorization": f"Bearer {TOKEN_A}"})
        assert result.status_code == 429
        assert "retry-after" in result.headers
        assert app.state.store.cache_get("cooldown:global")
        assert not app.state.credentials.clients
