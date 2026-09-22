import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.db import Store
from app.github import GitHubClient, GitHubError, META
from app.main import create_app
from app.schemas import normalize_repository_url
from app.services import Runner


def settings(tmp_path, **kwargs):
    return Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'test.sqlite3'}",
                    github_token=SecretStr("test-secret"), github_request_interval=0,
                    **kwargs)


def owner(owner_id="U1", kind="User"):
    return {"id": owner_id, "__typename": kind, "login": owner_id.lower(),
            "url": f"https://github.com/{owner_id}", "avatarUrl": None,
            "name": None, "bio": "<script>alert(1)</script>", "company": None,
            "location": None, "websiteUrl": "javascript:alert(1)"}


def fork(i=1, who=None, parent="ROOT"):
    return {"id": f"F{i}", "nameWithOwner": f"u{i}/repo", "url": f"https://github.com/u{i}/repo",
            "createdAt": f"2026-01-{i % 28 + 1:02d}T00:00:00Z", "pushedAt": None,
            "stargazerCount": i, "parent": {"id": parent}, "owner": who or owner(f"U{i}")}


def summary(i=0):
    return {"contributionCalendar": {"totalContributions": i}, "totalCommitContributions": i,
            "totalIssueContributions": 0, "totalPullRequestContributions": 0,
            "totalPullRequestReviewContributions": 0, "restrictedContributionsCount": 0}


def response(data, errors=None, cost=1, **headers):
    return httpx.Response(200, json={"data": {**data, "rateLimit": {"cost": cost, "remaining": 4000}},
                                      "errors": errors or []}, headers=headers)


class FakeGitHub:
    def __init__(self, pages=None, private=False):
        self.pages = pages if pages is not None else [[]]
        self.calls = []
        self.private = private

    def __call__(self, request):
        import json
        body = json.loads(request.content)
        self.calls.append(body)
        query, variables = body["query"], body["variables"]
        assert str(request.url) == "https://api.github.com/graphql"
        if "nodes(ids:" in query:
            return response({"nodes": [{"id": id, "contributionsCollection": summary()} for id in variables["ids"]]})
        root = {"id": "ROOT", "url": "https://github.com/up/repo", "nameWithOwner": "up/repo", "isPrivate": self.private}
        total = sum(len(p) for p in self.pages)
        if "pageInfo" not in query:
            return response({"repository": {**root, "forks": {"totalCount": total}}})
        index = int(variables["cursor"] or 0)
        return response({"repository": {**root, "forks": {"totalCount": total,
                        "nodes": self.pages[index][:variables["count"]],
                        "pageInfo": {"hasNextPage": index + 1 < len(self.pages), "endCursor": str(index + 1)}}}})


async def execute(tmp_path, fake, **kwargs):
    config = settings(tmp_path, **kwargs)
    store = Store(config.database_url)
    github = GitHubClient(config, store, httpx.MockTransport(fake))
    runner = Runner(config, store, github)
    task = store.create("https://github.com/up/repo", config.credential_id, config.max_forks)
    await runner.run(task["id"])
    await github.close()
    return store, task["id"], runner


@pytest.mark.parametrize("url", ["https://github.com/UP/Repo", "https://github.com/up/repo.git/", " https://github.com/up/repo/ "])
def test_normalize(url):
    assert normalize_repository_url(url) == "https://github.com/up/repo"


@pytest.mark.parametrize("url", ["http://github.com/up/repo", "https://evil.test/up/repo", "https://github.com.evil.test/up/repo",
 "https://user@github.com/up/repo", "https://github.com:443/up/repo", "https://github.com/up/repo/tree/main",
 "https://github.com/up/repo?q=x", "https://github.com/up/repo#x", "https://github.com/up/..", "https://github.com/up/%2e%2e"])
def test_reject_url(url):
    with pytest.raises(ValueError):
        normalize_repository_url(url)


async def test_pages_dedup_depth_organization_zero_and_cache(tmp_path):
    fake = FakeGitHub([[fork(1), fork(2, owner("U1")), fork(3, owner("O1", "Organization"))],
                       [fork(1), fork(4, parent="F1")]])
    store, task_id, runner = await execute(tmp_path, fake)
    task = store.get(task_id)
    assert task["status"] == "completed"
    assert len(store.rows(task_id)) == 3
    requests = [c for c in fake.calls if "nodes(ids:" in c["query"]]
    assert requests[0]["variables"]["ids"] == ["U1"]
    rows = {r["id"]: r for r in store.rows(task_id)}
    assert rows["F1"]["contribution"]["total"] == 0
    assert rows["F3"]["contribution"]["status"] == "not_applicable"
    start = datetime.fromisoformat(task["from"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(task["to"].replace("Z", "+00:00"))
    assert end - start == timedelta(days=365) and end.hour == 0
    config = runner.settings
    github = GitHubClient(config, store, httpx.MockTransport(fake))
    second = store.create(task["repository_url"], config.credential_id, config.max_forks)
    await Runner(config, store, github).run(second["id"])
    await github.close()
    assert len([c for c in fake.calls if "nodes(ids:" in c["query"]]) == 1
    store.engine.dispose()


async def test_truncation(tmp_path):
    fake = FakeGitHub([[fork(i) for i in range(100)], [fork(i) for i in range(100, 110)]])
    store, task_id, _ = await execute(tmp_path, fake, max_forks=103)
    task = store.get(task_id)
    assert task["status"] == "completed" and task["truncated"]
    assert task["total_direct_forks"] == 110 and len(store.rows(task_id)) == 103
    store.engine.dispose()


async def test_empty_private_missing(tmp_path):
    store, task_id, _ = await execute(tmp_path, FakeGitHub())
    assert store.get(task_id)["status"] == "completed" and store.rows(task_id) == []
    store.engine.dispose()
    store, task_id, _ = await execute(tmp_path, FakeGitHub(private=True))
    assert store.get(task_id)["error"]["code"] == "private_repository"
    store.engine.dispose()
    store, task_id, _ = await execute(tmp_path, lambda _: response({"repository": None}))
    assert store.get(task_id)["error"]["code"] == "not_found"
    store.engine.dispose()


async def test_partial_graphql_and_retry_only_failed(tmp_path):
    fake = FakeGitHub([[fork(1), fork(2)]])
    broken = True
    batches = []
    def handler(request):
        import json
        data = json.loads(request.content)
        if "nodes(ids:" in data["query"]:
            ids = data["variables"]["ids"]
            batches.append(ids)
            if broken:
                return response({"nodes": [{"id": "U1", "contributionsCollection": summary(5)}, None]},
                                [{"message": "hidden", "path": ["nodes", 1]}])
        return fake(request)
    config = settings(tmp_path)
    store = Store(config.database_url)
    github = GitHubClient(config, store, httpx.MockTransport(handler))
    runner = Runner(config, store, github)
    task = store.create("https://github.com/up/repo", config.credential_id, 1000)
    await runner.run(task["id"])
    assert store.get(task["id"])["status"] == "partial"
    assert store.rows(task["id"])[1]["contribution"]["status"] == "failed"
    broken = False
    store.update(task["id"], status="queued")
    await runner.run(task["id"])
    assert store.get(task["id"])["status"] == "completed"
    assert batches == [["U1", "U2"], ["U2"]]
    await github.close()
    store.engine.dispose()


async def test_timeout_split(tmp_path):
    fake = FakeGitHub([[fork(1), fork(2)]])
    batches = []
    def handler(request):
        import json
        body = json.loads(request.content)
        if "nodes(ids:" in body["query"]:
            ids = body["variables"]["ids"]
            batches.append(ids)
            if len(ids) > 1:
                return httpx.Response(504)
        return fake(request)
    store, task_id, _ = await execute(tmp_path, handler)
    assert store.get(task_id)["status"] == "completed"
    assert batches == [["U1", "U2"], ["U1"], ["U2"]]
    store.engine.dispose()


@pytest.mark.parametrize("kwargs", [{"max_task_requests": 1}, {"max_task_points": 1}])
async def test_budget(tmp_path, kwargs):
    store, task_id, _ = await execute(tmp_path, FakeGitHub([[fork()]]), **kwargs)
    assert store.get(task_id)["error"]["code"] == "budget_exhausted"
    assert store.get(task_id)["requests"] == 1
    store.engine.dispose()


async def test_secondary_rate_limit(tmp_path):
    config = settings(tmp_path, max_consecutive_rate_limits=2)
    store = Store(config.database_url)
    github = GitHubClient(config, store, httpx.MockTransport(lambda _: httpx.Response(429, headers={"Retry-After": "0"})))
    task = store.create("https://github.com/up/repo", config.credential_id, 1000)
    with pytest.raises(GitHubError, match="持续限流"):
        await github.query(task["id"], META, {})
    assert store.get(task["id"])["requests"] == 2
    assert store.cache_get(github.cooldown_key) is not None
    await github.close()
    store.engine.dispose()


async def test_primary_limit_and_http200_errors(tmp_path):
    config = settings(tmp_path)
    store = Store(config.database_url)
    github = GitHubClient(config, store, httpx.MockTransport(lambda _: response({"repository": None}, **{
        "x-ratelimit-remaining": "20", "x-ratelimit-reset": str(time.time() + 120)})))
    task = store.create("https://github.com/up/repo", config.credential_id, 1000)
    await github.query(task["id"], META, {})
    assert store.cache_get(github.cooldown_key)["until"] > time.time() + 100
    # Cancellation interrupts a waiting cooldown without making another request.
    store.update(task["id"], status="cancelled")
    from app.github import Cancelled
    with pytest.raises(Cancelled):
        await github.query(task["id"], META, {})
    await github.close()
    store.engine.dispose()


async def test_transient_failure_and_no_token_leak(tmp_path):
    config = settings(tmp_path, max_retries=1)
    store = Store(config.database_url)
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            raise httpx.ConnectError("test-secret", request=request)
        return httpx.Response(401, json={"message": "test-secret"})
    github = GitHubClient(config, store, httpx.MockTransport(handler))
    async def no_wait(*_): pass
    github.pause = no_wait
    task = store.create("https://github.com/up/repo", config.credential_id, 1000)
    await Runner(config, store, github).run(task["id"])
    result = store.get(task["id"])
    assert count == 2 and result["error"]["code"] == "token_invalid"
    assert "test-secret" not in str(result)
    await github.close()
    store.engine.dispose()


def test_api_dedup_limits_pagination_and_cancel(tmp_path):
    config = settings(tmp_path, max_queued_tasks=1, submissions_per_minute=20)
    app = create_app(config, start_worker=False)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/docs").status_code == 200
        assert client.post("/api/tasks", json={"repository_url": "https://evil.test/a/b"}).status_code == 422
        task = client.post("/api/tasks", json={"repository_url": "https://github.com/up/repo"}).json()
        assert "credential_id" not in task
        duplicate = client.post("/api/tasks", json={"repository_url": "https://github.com/up/repo.git"}).json()
        assert duplicate["id"] == task["id"] and duplicate["reused"]
        assert client.post("/api/tasks", json={"repository_url": "https://github.com/up/other"}).status_code == 429
        store = app.state.store
        rows = [{"id": f"F{i}", "created_at": "2026-01-01", "owner": {"id": f"U{i}", "login": f"user{i}"},
                 "contribution": {"status": "completed", "total": i}} for i in range(3)]
        rows.append({"id": "F4", "created_at": "2026-01-02", "owner": {"id": "U4", "login": "user4"}, "contribution": {"status": "failed"}})
        store.save_page(task["id"], rows, None, True, 4)
        url = f"/api/tasks/{task['id']}"
        result = client.get(url + "/results?sort=contributions&direction=asc").json()
        assert [r["id"] for r in result["items"]] == ["F0", "F1", "F2", "F4"]
        assert client.get(url + "/results?search=user1").json()["total"] == 1
        assert client.get(url + "/results?per_page=2&page=2").json()["items"]
        assert client.get(url + "/results?per_page=101").status_code == 422
        assert client.post(url + "/cancel").json()["status"] == "cancelled"
        assert client.post(url + "/retry").json()["status"] == "queued"
        assert client.get("/api/tasks/missing").status_code == 404
        assert client.post(url + "/cancel", headers={"Origin": "https://evil.test"}).status_code == 403


def test_submission_throttle_and_missing_token(tmp_path):
    config = settings(tmp_path, submissions_per_minute=1)
    with TestClient(create_app(config, start_worker=False)) as client:
        body = {"repository_url": "https://github.com/up/repo"}
        assert client.post("/api/tasks", json=body).status_code == 202
        assert client.post("/api/tasks", json=body).status_code == 429
    config.github_token = SecretStr("")
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.post("/api/tasks", json=body).status_code == 503


def test_worker_end_to_end_cache_and_restart(tmp_path):
    config = settings(tmp_path)
    fake = FakeGitHub([[fork()]])
    app = create_app(config, httpx.MockTransport(fake))
    with TestClient(app) as client:
        task = client.post("/api/tasks", json={"repository_url": "https://github.com/up/repo"}).json()
        url = f"/api/tasks/{task['id']}"
        for _ in range(200):
            status = client.get(url).json()
            if status["status"] == "completed": break
            time.sleep(.01)
        assert status["status"] == "completed"
        assert status["progress"]["owners_completed"] == 1
        assert client.post("/api/tasks", json={"repository_url": "https://github.com/up/repo"}).json()["reused"]
        # Simulate a crash after a committed page/contribution; recovery must not refetch.
        app.state.store.update(task["id"], status="running")
    before = len(fake.calls)
    with TestClient(create_app(config, httpx.MockTransport(fake))) as client:
        for _ in range(200):
            status = client.get(url).json()
            if status["status"] == "completed": break
            time.sleep(.01)
        assert status["status"] == "completed" and len(fake.calls) == before


async def test_cancel_inflight_then_resume(tmp_path):
    config = settings(tmp_path)
    store = Store(config.database_url)
    started, release = asyncio.Event(), asyncio.Event()
    fake = FakeGitHub([[fork()]])
    first = True
    async def handler(request):
        nonlocal first
        if first:
            first = False
            started.set()
            await release.wait()
        return fake(request)
    github = GitHubClient(config, store, httpx.MockTransport(handler))
    runner = Runner(config, store, github)
    task = store.create("https://github.com/up/repo", config.credential_id, 1000)
    await runner.start()
    await asyncio.wait_for(started.wait(), 2)
    store.update(task["id"], status="cancelled")
    await runner.cancel_current(task["id"])
    assert store.get(task["id"])["status"] == "cancelled"
    assert not fake.calls
    store.update(task["id"], status="queued")
    runner.wake.set()
    for _ in range(100):
        if store.get(task["id"])["status"] == "completed": break
        await asyncio.sleep(.01)
    assert store.get(task["id"])["status"] == "completed"
    await runner.stop()
    store.engine.dispose()


async def test_resume_saved_page_after_budget(tmp_path):
    fake = FakeGitHub([[fork(1)], [fork(2)]])
    config = settings(tmp_path, max_task_requests=2)
    store = Store(config.database_url)
    github = GitHubClient(config, store, httpx.MockTransport(fake))
    task = store.create("https://github.com/up/repo", config.credential_id, 1000)
    runner = Runner(config, store, github)
    await runner.run(task["id"])
    assert store.get(task["id"])["status"] == "partial"
    assert store.get(task["id"])["cursor"] == "1"
    assert len(store.rows(task["id"])) == 1
    store.update(task["id"], status="queued", requests=0, points=0)
    await runner.run(task["id"])
    assert store.get(task["id"])["status"] == "completed"
    cursors = [c["variables"]["cursor"] for c in fake.calls if "pageInfo" in c["query"]]
    assert cursors == [None, "1"]
    await github.close()
    store.engine.dispose()


async def test_http200_rate_limit_and_global_cooldown(tmp_path):
    config = settings(tmp_path, max_consecutive_rate_limits=1)
    store = Store(config.database_url)
    github = GitHubClient(config, store, httpx.MockTransport(lambda _: httpx.Response(200, json={
        "data": None, "errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]})))
    task = store.create("https://github.com/up/repo", config.credential_id, 1000)
    with pytest.raises(GitHubError):
        await github.query(task["id"], META, {})
    assert store.cache_get(github.cooldown_key)["until"] > time.time() + 55
    # A new client sharing this credential/store observes the same persisted cooldown.
    other = GitHubClient(config, store, httpx.MockTransport(FakeGitHub()))
    assert store.cache_get(other.cooldown_key) is not None
    await github.close()
    await other.close()
    store.engine.dispose()


async def test_cache_credentials_and_expiry(tmp_path):
    config = settings(tmp_path)
    store = Store(config.database_url)
    store.cache_put("expired", {"private": "value"}, -1)
    assert store.cache_get("expired") is None
    task = store.create("https://github.com/up/repo", "different-credential", 1000)
    github = GitHubClient(config, store, httpx.MockTransport(FakeGitHub()))
    await Runner(config, store, github).run(task["id"])
    assert store.get(task["id"])["error"]["code"] == "credential_changed"
    assert store.get(task["id"])["requests"] == 0
    await github.close()
    store.engine.dispose()
