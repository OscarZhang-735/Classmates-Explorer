import asyncio
import time

from app.config import Settings
from app.db import ACTIVE, Store
from app.github import (CONTRIBUTIONS, FORKS, META, BatchTooLarge, Cancelled,
                        GitHubClient, GitHubError, collected_at)


class ContributionService:
    """Independent summary provider; daily calendar data can be added here later."""

    def __init__(self, settings: Settings, store: Store, github: GitHubClient):
        self.settings, self.store, self.github = settings, store, github

    def cache_key(self, task, owner_id):
        return f"contribution:{task['credential_id']}:{owner_id}:{task['from']}:{task['to']}"

    async def fetch(self, task_id: str, ids: list[str], singleton_retries=0):
        task = self.github.check(task_id)
        try:
            result = await self.github.query(task_id, CONTRIBUTIONS,
                                            {"ids": ids, "from": task["from"], "to": task["to"]}, batch=True)
        except BatchTooLarge as error:
            if len(ids) > 1:
                middle = len(ids) // 2
                await self.fetch(task_id, ids[:middle])
                await self.fetch(task_id, ids[middle:])
            elif singleton_retries < self.settings.max_retries:
                await self.github.pause(task_id, 2 ** singleton_retries)
                await self.fetch(task_id, ids, singleton_retries + 1)
            else:
                self.store.set_contribution(task_id, ids[0], {"status": "failed", "error": error.public()})
            return
        self.github.check(task_id)
        nodes = result["data"].get("nodes") or []
        # GraphQL may return partial data and field errors with HTTP 200.
        failed_indexes = set()
        global_error = False
        for error in result["errors"]:
            path = error.get("path") or []
            if len(path) >= 2 and path[0] == "nodes" and isinstance(path[1], int):
                failed_indexes.add(path[1])
            elif path and path[0] == "rateLimit":
                continue
            else:
                global_error = True
        for index, owner_id in enumerate(ids):
            node = nodes[index] if index < len(nodes) else None
            summary = node.get("contributionsCollection") if node else None
            required = ("totalCommitContributions", "totalIssueContributions",
                        "totalPullRequestContributions", "totalPullRequestReviewContributions",
                        "restrictedContributionsCount")
            valid = (summary and node.get("id") == owner_id and index not in failed_indexes
                     and not global_error and all(summary.get(k) is not None for k in required)
                     and (summary.get("contributionCalendar") or {}).get("totalContributions") is not None)
            if not valid:
                self.store.set_contribution(task_id, owner_id, {
                    "status": "failed", "error": {"code": "contribution_unavailable", "message": "无法获取该用户的贡献统计"}})
                continue
            value = {"status": "completed", "from": task["from"], "to": task["to"],
                     "collected_at": collected_at(),
                     "total": summary["contributionCalendar"]["totalContributions"],
                     "commits": summary["totalCommitContributions"],
                     "issues": summary["totalIssueContributions"],
                     "pull_requests": summary["totalPullRequestContributions"],
                     "reviews": summary["totalPullRequestReviewContributions"],
                     "restricted": summary["restrictedContributionsCount"]}
            self.store.cache_put(self.cache_key(task, owner_id), value, self.settings.contribution_cache_seconds)
            self.store.set_contribution(task_id, owner_id, value)

    async def run(self, task_id):
        task = self.github.check(task_id)
        owners = {}
        for row in self.store.rows(task_id):
            if row["owner"]["type"] == "User" and row["contribution"]["status"] != "completed":
                owners[row["owner"]["id"]] = row["owner"]
        pending = []
        for owner_id in owners:
            cached = self.store.cache_get(self.cache_key(task, owner_id))
            if cached:
                self.store.set_contribution(task_id, owner_id, cached)
            else:
                pending.append(owner_id)
        batch_size = self.settings.contribution_batch_size
        for offset in range(0, len(pending), batch_size):
            await self.fetch(task_id, pending[offset:offset + batch_size])


class Runner:
    def __init__(self, settings: Settings, store: Store, github: GitHubClient):
        self.settings, self.store, self.github = settings, store, github
        self.contributions = ContributionService(settings, store, github)
        self.wake = asyncio.Event()
        self.worker = None
        self.current = None
        self.current_id = None
        self.stopping = False

    async def start(self):
        for task in self.store.tasks(ACTIVE):
            self.store.update(task["id"], status="queued")
        self.worker = asyncio.create_task(self.loop())
        self.wake.set()

    async def stop(self):
        self.stopping = True
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
        await self.github.close()

    async def loop(self):
        while True:
            self.wake.clear()
            queued = self.store.tasks(("queued",))
            if not queued:
                await self.wake.wait()
                continue
            task_id = queued[0]["id"]
            self.current_id = task_id
            self.current = asyncio.create_task(self.run(task_id))
            try:
                await self.current
            except asyncio.CancelledError:
                if self.stopping:
                    raise
            finally:
                self.current = None
                self.current_id = None

    async def cancel_current(self, task_id):
        if self.current_id == task_id and self.current and not self.current.done():
            self.current.cancel()
            try:
                await self.current
            except asyncio.CancelledError:
                pass

    def owner(self, raw: dict, credential: str):
        key = f"owner:{credential}:{raw['id']}"
        cached = self.store.cache_get(key)
        if cached:
            # Keep stable ID caching without retaining a stale renamed login/URL.
            return {**cached, "login": raw["login"], "url": raw["url"]}
        value = {"id": raw["id"], "login": raw["login"], "type": raw["__typename"],
                 "url": raw["url"], "avatar_url": raw.get("avatarUrl"), "name": raw.get("name"),
                 "bio": raw.get("bio") or raw.get("description"), "company": raw.get("company"),
                 "location": raw.get("location"), "website_url": raw.get("websiteUrl"),
                 "collected_at": collected_at()}
        self.store.cache_put(key, value, self.settings.owner_cache_seconds)
        return value

    async def run(self, task_id):
        try:
            task = self.github.check(task_id)
            self.store.update(task_id, status="running", error=None, resume_at=None)
            owner, repo = task["repository_url"].split("/")[-2:]
            if not task.get("repository_id"):
                result = await self.github.query(task_id, META, {"owner": owner, "repo": repo})
                self.github.check(task_id)
                repository = result["data"].get("repository")
                self.require_public(repository)
                task = self.store.update(task_id, repository_id=repository["id"],
                                         canonical_url=repository["url"],
                                         total_direct_forks=repository["forks"]["totalCount"])
            while not task["forks_done"]:
                self.store.update(task_id, phase="forks")
                existing = {row["id"] for row in self.store.rows(task_id)}
                count = min(100, task["limit"] - len(existing))
                if count <= 0:
                    self.store.update(task_id, forks_done=True, truncated=True)
                    break
                result = await self.github.query(task_id, FORKS, {
                    "owner": owner, "repo": repo, "cursor": task["cursor"], "count": count})
                self.github.check(task_id)
                repository = result["data"].get("repository")
                self.require_public(repository)
                connection = repository["forks"]
                rows = []
                for node in connection["nodes"]:
                    if not node or node["id"] in existing:
                        continue
                    # Explicitly enforce depth one even if the upstream response changes.
                    if not node.get("parent") or node["parent"]["id"] != task["repository_id"]:
                        continue
                    if not node.get("owner"):
                        raise GitHubError("owner_unavailable", "GitHub 未返回 Fork 所有者，已保留分页检查点")
                    profile = self.owner(node["owner"], task["credential_id"])
                    rows.append({"id": node["id"], "name": node["nameWithOwner"], "url": node["url"],
                                 "created_at": node["createdAt"], "pushed_at": node.get("pushedAt"),
                                 "stars": node["stargazerCount"], "owner": profile,
                                 "collected_at": collected_at(),
                                 "contribution": {"status": "pending" if profile["type"] == "User" else "not_applicable"}})
                    existing.add(node["id"])
                    if len(existing) >= task["limit"]:
                        break
                page = connection["pageInfo"]
                done = not page["hasNextPage"] or len(existing) >= task["limit"]
                if not done and (not page["endCursor"] or page["endCursor"] == task["cursor"]):
                    raise GitHubError("invalid_cursor", "GitHub 分页未前进，已停止查询")
                self.store.save_page(task_id, rows, page["endCursor"], done, connection["totalCount"])
                task = self.store.get(task_id)
            self.store.update(task_id, phase="contributions")
            await self.contributions.run(task_id)
            self.github.check(task_id)
            failed = any(row["contribution"]["status"] == "failed" for row in self.store.rows(task_id))
            self.store.update(task_id, status="partial" if failed else "completed", phase="done",
                              completed_at=time.time(), retryable=failed,
                              error={"code": "partial_contributions", "message": "部分用户贡献获取失败，可重试"} if failed else None)
        except Cancelled:
            pass
        except GitHubError as error:
            self.fail(task_id, error)
        except Exception:
            # Do not log transport/request objects that might include credentials.
            self.fail(task_id, GitHubError("internal_error", "任务处理异常，已保存结果和检查点"))

    def fail(self, task_id, error):
        if self.store.get(task_id)["status"] == "cancelled":
            return
        self.store.update(task_id, status="partial" if self.store.rows(task_id) else "failed",
                          error=error.public(), retryable=error.retryable, resume_at=None,
                          completed_at=time.time())

    @staticmethod
    def require_public(repository):
        if not repository:
            raise GitHubError("not_found", "仓库不存在或不可访问", False)
        if repository.get("isPrivate"):
            raise GitHubError("private_repository", "仅支持公开仓库", False)
