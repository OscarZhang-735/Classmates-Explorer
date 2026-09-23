"""Explicit opt-in smoke test; mock tests never contact GitHub."""
import os

import pytest

from app.config import Settings
from app.db import Store
from app.github import GitHubClient
from app.schemas import normalize_repository_url
from app.services import Runner


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("RUN_GITHUB_SMOKE") != "1", reason="Set RUN_GITHUB_SMOKE=1 to contact GitHub")
async def test_github_smoke(tmp_path):
    config = Settings(app_mode="local", database_url=f"sqlite:///{tmp_path / 'live.sqlite3'}", max_forks=3,
                      max_task_requests=20, max_task_points=30)
    if not config.github_token.get_secret_value():
        pytest.skip("GITHUB_TOKEN is not configured")
    url = normalize_repository_url(os.environ.get("GITHUB_SMOKE_REPOSITORY", "https://github.com/octocat/git-consortium"))
    store = Store(config.database_url)
    github = GitHubClient(config, store)
    task = store.create(url, config.credential_id, config.max_forks)
    try:
        import asyncio
        async with asyncio.timeout(90):
            await Runner(config, store, github).run(task["id"])
        result = store.get(task["id"])
        assert result["status"] == "completed", result["error"]
        assert result["repository_id"] and result["total_direct_forks"] is not None
        assert len(store.rows(task["id"])) <= 3
        for row in store.rows(task["id"]):
            assert row["contribution"]["status"] in ("completed", "not_applicable")
    finally:
        await github.close()
        store.engine.dispose()
