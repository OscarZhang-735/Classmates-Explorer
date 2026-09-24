"""Fork pages shrink without skipping data, bypassing budgets or looping forever."""
import json

import httpx
import pytest

from app.db import Store
from app.github import GitHubClient
from app.services import Runner
from tests.test_explorer import FakeGitHub, execute, fork, response, settings


class SizedForks(FakeGitHub):
    def __init__(self, failure=504, threshold=25):
        super().__init__([[fork(i) for i in range(1, 30)]])
        self.failure, self.threshold = failure, threshold
        self.attempts = []

    def __call__(self, request):
        body = json.loads(request.content)
        if "pageInfo" not in body["query"]:
            return super().__call__(request)
        variables = body["variables"]
        count, cursor = variables["count"], variables["cursor"]
        self.attempts.append((cursor, count))
        if count > self.threshold:
            if self.failure == "timeout":
                raise httpx.ReadTimeout("simulated", request=request)
            if self.failure == "resource":
                # Even if GitHub supplied partial data, its cursor must not commit.
                return response({"repository": {"id": "ROOT", "isPrivate": False, "forks": {
                    "totalCount": 29, "nodes": [fork(999)],
                    "pageInfo": {"hasNextPage": True, "endCursor": "999"}}}},
                    errors=[{"message": "Resource limit exceeded", "type": "RESOURCE_LIMITS_EXCEEDED"}])
            return httpx.Response(self.failure)
        start = int(cursor or 0)
        nodes = self.pages[0][start:start + count]
        end = start + len(nodes)
        return response({"repository": {"id": "ROOT", "isPrivate": False, "forks": {
            "totalCount": 29, "nodes": nodes,
            "pageInfo": {"hasNextPage": end < 29, "endCursor": str(end)}}}})


@pytest.mark.parametrize("failure", [502, 503, 504, "timeout", "resource"])
async def test_fork_pages_shrink_without_skips(tmp_path, failure):
    fake = SizedForks(failure)
    store, task_id, _ = await execute(tmp_path, fake, max_retries=0)
    try:
        task = store.get(task_id)
        assert task["status"] == "completed", task["error"]
        assert fake.attempts == [(None, 100), (None, 50), (None, 25), ("25", 25)]
        assert task["fork_page_size"] == 25
        assert {r["id"] for r in store.rows(task_id)} == {f"F{i}" for i in range(1, 30)}
        assert task["requests"] == 8  # metadata + four page attempts + three contribution batches
        assert task["points"] >= 6
    finally:
        store.engine.dispose()


async def test_smaller_page_survives_budget_stop_and_restart(tmp_path):
    cfg = settings(tmp_path, max_task_requests=2, max_retries=0)
    fake = SizedForks()
    store = Store(cfg.database_url)
    github = GitHubClient(cfg, store, httpx.MockTransport(fake))
    task_id = store.create("https://github.com/up/repo", cfg.credential_id, 1000)["id"]
    await Runner(cfg, store, github).run(task_id)
    task = store.get(task_id)
    assert task["error"]["code"] == "budget_exhausted"
    assert task["requests"] == 2 and task["fork_page_size"] == 50
    assert task["cursor"] is None and not store.rows(task_id)
    await github.close()
    store.engine.dispose()
    # Reopen the database, like a server restart. Explicit retry resets the budget.
    cfg.max_task_requests = 30
    store = Store(cfg.database_url)
    github = GitHubClient(cfg, store, httpx.MockTransport(fake))
    try:
        store.update(task_id, status="queued", requests=0, points=0)
        await Runner(cfg, store, github).run(task_id)
        assert fake.attempts[:3] == [(None, 100), (None, 50), (None, 25)]
        assert store.get(task_id)["status"] == "completed"
        assert len(store.rows(task_id)) == 29
    finally:
        await github.close()
        store.engine.dispose()


@pytest.mark.parametrize("failure", [504, "resource", "timeout"])
async def test_smallest_page_failure_is_bounded(tmp_path, failure):
    fake = SizedForks(failure, threshold=0)
    store, task_id, _ = await execute(tmp_path, fake, max_retries=0)
    try:
        task = store.get(task_id)
        assert task["status"] == "failed" and task["retryable"]
        assert [n for _, n in fake.attempts] == [100, 50, 25, 12, 6, 3, 1]
        assert task["requests"] == 8
        assert task["cursor"] is None and task["fork_page_size"] == 1
    finally:
        store.engine.dispose()


@pytest.mark.parametrize("status,code", [(401, "token_invalid"), (403, "access_denied")])
async def test_auth_failures_do_not_shrink_pages(tmp_path, status, code):
    fake = SizedForks(status, threshold=0)
    store, task_id, _ = await execute(tmp_path, fake, max_retries=0)
    try:
        assert store.get(task_id)["error"]["code"] == code
        assert fake.attempts == [(None, 100)]
    finally:
        store.engine.dispose()
