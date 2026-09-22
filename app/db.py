import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import JSON, Float, String, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

ACTIVE = ("queued", "running", "waiting_rate_limit")


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    repository_url: Mapped[str] = mapped_column(String, index=True)
    credential_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON)


class Fork(Base):
    __tablename__ = "forks"
    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    repository_id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class Cache(Base):
    __tablename__ = "cache"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    expires_at: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON)


class Store:
    def __init__(self, url: str):
        options = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, **options)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _):
                cursor = connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def serialize(task: Task) -> dict:
        return {**task.payload, "id": task.id, "repository_url": task.repository_url,
                "status": task.status, "created_at": task.created_at,
                "updated_at": task.updated_at, "credential_id": task.credential_id}

    def get(self, task_id: str) -> dict | None:
        with self.session() as session:
            task = session.get(Task, task_id)
            return self.serialize(task) if task else None

    def tasks(self, statuses=None) -> list[dict]:
        with self.session() as session:
            query = select(Task).order_by(Task.created_at)
            if statuses:
                query = query.where(Task.status.in_(statuses))
            return [self.serialize(t) for t in session.scalars(query)]

    def create(self, url: str, credential: str, limit: int) -> dict:
        now = datetime.now(timezone.utc)
        # A fixed daily UTC endpoint makes same-day jobs/cache intervals identical.
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        iso = lambda d: d.isoformat().replace("+00:00", "Z")
        task = Task(id=uuid.uuid4().hex, repository_url=url, credential_id=credential,
                    status="queued", created_at=now.timestamp(), updated_at=now.timestamp(),
                    payload={"phase": "repository", "from": iso(end - timedelta(days=365)),
                             "to": iso(end), "date": end.date().isoformat(), "limit": limit,
                             "cursor": None, "forks_done": False, "total_direct_forks": None,
                             "truncated": False, "error": None, "retryable": True,
                             "resume_at": None, "requests": 0, "points": 0,
                             "rate_limit_streak": 0, "completed_at": None})
        with self.session() as session:
            session.add(task)
            session.commit()
        return self.serialize(task)

    def update(self, task_id: str, **values) -> dict:
        with self.session() as session:
            task = session.get(Task, task_id)
            if "status" in values:
                task.status = values.pop("status")
            task.payload = {**task.payload, **values}
            task.updated_at = time.time()
            session.commit()
            return self.serialize(task)

    def rows(self, task_id: str) -> list[dict]:
        with self.session() as session:
            query = select(Fork).where(Fork.task_id == task_id)
            return [row.payload for row in session.scalars(query)]

    def save_page(self, task_id: str, rows: list[dict], cursor, done: bool, total: int):
        # Commit rows and cursor together: crash recovery never skips a page.
        with self.session() as session:
            for row in rows:
                key = (task_id, row["id"])
                if not session.get(Fork, key):
                    session.add(Fork(task_id=task_id, repository_id=row["id"],
                                     owner_id=row["owner"]["id"], payload=row))
            task = session.get(Task, task_id)
            task.payload = {**task.payload, "cursor": cursor, "forks_done": done,
                            "total_direct_forks": total, "truncated": total > task.payload["limit"]}
            task.updated_at = time.time()
            session.commit()

    def set_contribution(self, task_id: str, owner_id: str, value: dict):
        with self.session() as session:
            query = select(Fork).where(Fork.task_id == task_id, Fork.owner_id == owner_id)
            for row in session.scalars(query):
                row.payload = {**row.payload, "contribution": value}
            session.commit()

    def cache_get(self, key: str) -> dict | None:
        with self.session() as session:
            entry = session.get(Cache, key)
            return entry.payload if entry and entry.expires_at > time.time() else None

    def cache_put(self, key: str, value: dict, ttl: float):
        with self.session() as session:
            session.merge(Cache(key=key, payload=value, expires_at=time.time() + ttl))
            session.commit()
