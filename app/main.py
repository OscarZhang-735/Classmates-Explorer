import asyncio
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.db import ACTIVE, Store
from app.github import GitHubClient
from app.schemas import TaskInput
from app.services import Runner

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
            "configured": bool(settings.github_token.get_secret_value()), "limit": settings.max_forks})

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
                        and task["credential_id"] == settings.credential_id and task["limit"] == settings.max_forks):
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
                      sort: Literal["created", "contributions"] = "created",
                      direction: Literal["asc", "desc"] = "desc"):
        get_task(task_id)
        rows = app.state.store.rows(task_id)
        rows = [r for r in rows if search.casefold() in r["owner"]["login"].casefold()]
        if sort == "contributions":
            known = [r for r in rows if r["contribution"].get("total") is not None]
            unknown = [r for r in rows if r["contribution"].get("total") is None]
            known.sort(key=lambda r: (r["contribution"]["total"], r["id"]), reverse=direction == "desc")
            rows = known + sorted(unknown, key=lambda r: r["id"])
        else:
            rows.sort(key=lambda r: (r["created_at"], r["id"]), reverse=direction == "desc")
        return {"items": rows[(page - 1) * per_page:page * per_page], "total": len(rows),
                "page": page, "per_page": per_page}

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
