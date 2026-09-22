import asyncio
import csv
import io
import json
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.db import ACTIVE, Store
from app.github import GitHubClient
from app.schemas import ImportSnapshot, TaskInput
from app.services import Runner
from pydantic import ValidationError

ROOT = Path(__file__).parent


def create_app(settings: Settings | None = None, transport=None, start_worker=True):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        store = Store(settings.database_url)
        github = GitHubClient(settings, store, transport)
        runner = Runner(settings, store, github)
        app.state.store, app.state.runner = store, runner
        app.state.submit_lock = asyncio.Lock()
        app.state.submissions = defaultdict(deque)
        if start_worker:
            await runner.start()
        try:
            yield
        finally:
            await runner.stop()
            store.engine.dispose()

    app = FastAPI(title="Classmates Explorer", version="0.1.0", lifespan=lifespan,
                  description="公开仓库的一级 Fork、Owner 资料和近 365 天全站贡献。单进程本地运行。")
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        # Local write endpoints must not accept cross-site form/fetch submissions.
        origin = request.headers.get("origin")
        if request.method == "POST" and origin and origin != str(request.base_url).rstrip("/"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "仅允许同源请求"}, status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={
            "configured": bool(settings.github_token.get_secret_value()), "limit": settings.max_forks,
            "max_import_bytes": settings.max_import_bytes,
            "max_import_mb": settings.max_import_bytes // 1024 // 1024})

    def get_task(task_id):
        task = app.state.store.get(task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        return task

    def public_task(task):
        value = {k: v for k, v in task.items() if k not in ("credential_id", "cursor")}
        rows = app.state.store.rows(task["id"])
        owners = {row["owner"]["id"]: row["contribution"]["status"] for row in rows}
        value["progress"] = {"forks_fetched": len(rows), "owners_total": len(owners),
                             "owners_completed": sum(v in ("completed", "not_applicable") for v in owners.values()),
                             "owners_failed": sum(v == "failed" for v in owners.values())}
        value["omitted_forks"] = max(0, (task["total_direct_forks"] or 0) - task["limit"])
        return value

    def export_snapshot(task, rows):
        return {"format": "classmates-explorer-snapshot", "version": 1,
                "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "task": {"source_task_id": task["id"], "repository_url": task["repository_url"],
                         "canonical_url": task.get("canonical_url"), "source_status": task["status"],
                         "from": task["from"], "to": task["to"],
                         "total_direct_forks": task["total_direct_forks"],
                         "truncated": task["truncated"], "limit": min(task["limit"], 1000),
                         "forks_done": task["forks_done"], "data_version": task.get("data_version", 1)},
                "results": sorted(rows, key=lambda row: (row["created_at"], row["id"]), reverse=True)}

    def filename(task, suffix):
        slug = "-".join(task["repository_url"].split("/")[-2:])
        return f"{slug}-{task['date']}.{suffix}"

    def csv_value(value):
        if value is None:
            return ""
        value = str(value)
        # Prevent spreadsheet software from interpreting imported profile text as a formula.
        return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value

    def admission(request):
        now = time.monotonic()
        key = request.client.host if request.client else "local"
        bucket = app.state.submissions[key]
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        if len(bucket) >= settings.submissions_per_minute:
            raise HTTPException(429, "任务提交过于频繁", headers={"Retry-After": "60"})
        bucket.append(now)

    def check_queue():
        if len(app.state.store.tasks(("queued",))) >= settings.max_queued_tasks:
            raise HTTPException(429, "任务队列已满，请稍后重试", headers={"Retry-After": "30"})

    @app.post("/api/tasks", status_code=202)
    async def submit(body: TaskInput, request: Request):
        async with app.state.submit_lock:
            admission(request)
            today = datetime.now(timezone.utc).date().isoformat()
            for task in reversed(app.state.store.tasks()):
                if (task["repository_url"] == body.repository_url and task["date"] == today
                        and task["credential_id"] == settings.credential_id and task["limit"] == settings.max_forks
                        and task.get("data_version") == 2):
                    cached = (task["status"] == "completed" and
                              time.time() - (task["completed_at"] or 0) < settings.result_cache_seconds)
                    if task["status"] in ACTIVE or cached:
                        return {**public_task(task), "reused": True}
            if not settings.github_token.get_secret_value():
                raise HTTPException(503, "请先在服务端 .env 中配置 GITHUB_TOKEN")
            check_queue()
            task = app.state.store.create(body.repository_url, settings.credential_id, settings.max_forks)
            app.state.runner.wake.set()
            return {**public_task(task), "reused": False}

    @app.get("/api/tasks/{task_id}")
    async def status(task_id: str):
        return public_task(get_task(task_id))

    @app.get("/api/tasks/{task_id}/results")
    async def results(task_id: str, page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=100),
                      search: str = Query("", max_length=100),
                      sort: Literal["created", "contributions", "repositories", "stars", "account_created"] = "created",
                      direction: Literal["asc", "desc"] = "desc",
                      account_created_from: date | None = None, account_created_to: date | None = None,
                      fork_created_from: date | None = None, fork_created_to: date | None = None):
        get_task(task_id)
        if account_created_from and account_created_to and account_created_from > account_created_to:
            raise HTTPException(422, "账号创建时间的起始日期不能晚于结束日期")
        if fork_created_from and fork_created_to and fork_created_from > fork_created_to:
            raise HTTPException(422, "Fork 创建时间的起始日期不能晚于结束日期")
        rows = app.state.store.rows(task_id)
        rows = [r for r in rows if search.casefold() in r["owner"]["login"].casefold()]

        def parsed_date(value):
            if not value:
                return None
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
            except (TypeError, ValueError):
                return None

        def in_range(value, start, end):
            candidate = parsed_date(value)
            return candidate is not None and (start is None or candidate >= start) and (end is None or candidate <= end)

        if account_created_from or account_created_to:
            rows = [r for r in rows if in_range(r["owner"].get("account_created_at"),
                                                 account_created_from, account_created_to)]
        if fork_created_from or fork_created_to:
            rows = [r for r in rows if in_range(r.get("created_at"), fork_created_from, fork_created_to)]

        getters = {
            "created": lambda r: r.get("created_at"),
            "contributions": lambda r: r["contribution"].get("total"),
            "repositories": lambda r: r["owner"].get("public_repositories"),
            "stars": lambda r: r.get("stars"),
            "account_created": lambda r: r["owner"].get("account_created_at"),
        }
        getter = getters[sort]
        known = [row for row in rows if getter(row) is not None]
        unknown = [row for row in rows if getter(row) is None]
        known.sort(key=lambda row: (getter(row), row["id"]), reverse=direction == "desc")
        rows = known + sorted(unknown, key=lambda row: row["id"])
        return {"items": rows[(page - 1) * per_page:page * per_page], "total": len(rows),
                "page": page, "per_page": per_page}

    @app.get("/api/tasks/{task_id}/export")
    async def export(task_id: str, format: Literal["json", "csv"] = "json"):
        task = get_task(task_id)
        if task["status"] != "completed":
            raise HTTPException(409, "仅查询完成的任务可以导出")
        rows = app.state.store.rows(task_id)
        if format == "json":
            # Validate and canonicalize our own output so a JSON export is guaranteed importable.
            snapshot = ImportSnapshot.model_validate(export_snapshot(task, rows)).model_dump(mode="json", by_alias=True)
            content = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
            return Response(content, media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="{filename(task, "json")}"'})
        output = io.StringIO(newline="")
        fields = ["fork_id", "fork_name", "fork_url", "fork_created_at", "fork_pushed_at", "stars",
                  "owner_id", "owner_login", "owner_type", "owner_name", "owner_url", "avatar_url",
                  "bio", "company", "location", "website_url", "account_created_at",
                  "public_repositories", "owner_collected_at",
                  "contribution_status", "contribution_from", "contribution_to", "contribution_collected_at",
                  "total", "commits", "pull_requests", "issues", "reviews", "restricted", "error"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: (item["created_at"], item["id"]), reverse=True):
            owner, contribution = row["owner"], row["contribution"]
            values = {"fork_id": row["id"], "fork_name": row["name"], "fork_url": row["url"],
                      "fork_created_at": row["created_at"], "fork_pushed_at": row.get("pushed_at"),
                      "stars": row["stars"], "owner_id": owner["id"], "owner_login": owner["login"],
                      "owner_type": owner["type"], "owner_name": owner.get("name"), "owner_url": owner["url"],
                      "avatar_url": owner.get("avatar_url"), "bio": owner.get("bio"),
                      "company": owner.get("company"), "location": owner.get("location"),
                      "website_url": owner.get("website_url"),
                      "account_created_at": owner.get("account_created_at"),
                      "public_repositories": owner.get("public_repositories"),
                      "owner_collected_at": owner.get("collected_at"),
                      "contribution_status": contribution["status"], "contribution_from": contribution.get("from"),
                      "contribution_to": contribution.get("to"),
                      "contribution_collected_at": contribution.get("collected_at"),
                      "total": contribution.get("total"), "commits": contribution.get("commits"),
                      "pull_requests": contribution.get("pull_requests"), "issues": contribution.get("issues"),
                      "reviews": contribution.get("reviews"), "restricted": contribution.get("restricted"),
                      "error": (contribution.get("error") or {}).get("message")}
            writer.writerow({key: csv_value(value) for key, value in values.items()})
        return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename(task, "csv")}"'})

    @app.post("/api/imports", status_code=201)
    async def import_snapshot(request: Request):
        admission(request)
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > settings.max_import_bytes:
                raise HTTPException(413, f"导入文件不能超过 {settings.max_import_bytes // 1024 // 1024} MB")
            chunks.append(chunk)
        if not size:
            raise HTTPException(422, "导入文件为空")
        try:
            snapshot = ImportSnapshot.model_validate_json(b"".join(chunks))
        except ValidationError:
            raise HTTPException(422, "不是有效的 Classmates Explorer v1 JSON 快照") from None
        data = snapshot.model_dump(mode="json", by_alias=True)
        task = app.state.store.import_snapshot(data)
        return public_task(task)

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel(task_id: str):
        task = get_task(task_id)
        if task["status"] in ACTIVE:
            task = app.state.store.update(task_id, status="cancelled", retryable=True, resume_at=None)
            await app.state.runner.cancel_current(task_id)
        return public_task(task)

    @app.post("/api/tasks/{task_id}/retry", status_code=202)
    async def retry(task_id: str, request: Request):
        async with app.state.submit_lock:
            task = get_task(task_id)
            if task["status"] in ACTIVE:
                return public_task(task)
            if task["status"] not in ("partial", "failed", "cancelled") or not task["retryable"]:
                raise HTTPException(409, "该任务不可重试，请创建新任务")
            if task["credential_id"] != settings.credential_id:
                raise HTTPException(409, "凭据已更换，请创建新任务")
            admission(request)
            check_queue()
            # Explicit retry starts a new bounded run, preserving successful rows/cursors.
            task = app.state.store.update(task_id, status="queued", error=None, resume_at=None,
                                          requests=0, points=0, rate_limit_streak=0, completed_at=None)
            app.state.runner.wake.set()
            return public_task(task)

    return app


app = create_app()
