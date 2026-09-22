import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator, field_validator


def normalize_repository_url(value: str) -> str:
    try:
        url = urlsplit(value.strip())
        if (url.scheme != "https" or url.netloc.lower() != "github.com"
                or url.query or url.fragment):
            raise ValueError
        parts = url.path.rstrip("/").split("/")
        if len(parts) != 3 or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", parts[1]):
            raise ValueError
        repo = parts[2].removesuffix(".git")
        if repo in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo):
            raise ValueError
        return f"https://github.com/{parts[1].lower()}/{repo.lower()}"
    except ValueError:
        raise ValueError("请输入 https://github.com/owner/repo 格式的公开仓库 URL") from None


class TaskInput(BaseModel):
    repository_url: str = Field(max_length=300)

    @field_validator("repository_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return normalize_repository_url(value)


class ImportOwner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    login: str = Field(min_length=1, max_length=100)
    type: Literal["User", "Organization"]
    url: HttpUrl
    avatar_url: HttpUrl | None = None
    name: str | None = Field(None, max_length=300)
    bio: str | None = Field(None, max_length=2000)
    company: str | None = Field(None, max_length=300)
    location: str | None = Field(None, max_length=300)
    website_url: str | None = Field(None, max_length=500)
    collected_at: datetime | None = None

    @field_validator("url")
    @classmethod
    def github_profile(cls, value: HttpUrl):
        if (value.scheme != "https" or value.host != "github.com" or value.username
                or value.password or value.query or value.fragment):
            raise ValueError("owner URL 必须是 github.com HTTPS 地址")
        return value

    @field_validator("avatar_url")
    @classmethod
    def secure_avatar(cls, value: HttpUrl | None):
        if value is not None and value.scheme != "https":
            raise ValueError("头像必须使用 HTTPS")
        return value

    @field_validator("website_url")
    @classmethod
    def safe_website_text(cls, value: str | None):
        if value is None:
            return value
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("个人网站字段无效")
        parsed = urlsplit(value)
        if parsed.scheme and parsed.scheme.lower() not in ("http", "https"):
            raise ValueError("个人网站仅允许 HTTP(S) 地址或不带协议的公开文本")
        if parsed.username or parsed.password:
            raise ValueError("个人网站不能包含登录信息")
        return value


class ImportContribution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["pending", "completed", "failed", "not_applicable"]
    from_: datetime | None = Field(None, alias="from")
    to: datetime | None = None
    collected_at: datetime | None = None
    total: int | None = Field(None, ge=0)
    commits: int | None = Field(None, ge=0)
    issues: int | None = Field(None, ge=0)
    pull_requests: int | None = Field(None, ge=0)
    reviews: int | None = Field(None, ge=0)
    restricted: int | None = Field(None, ge=0)
    error: dict | None = None

    @model_validator(mode="after")
    def completed_has_values(self):
        fields = (self.from_, self.to, self.collected_at, self.total, self.commits,
                  self.issues, self.pull_requests, self.reviews, self.restricted)
        if self.status == "completed" and any(value is None for value in fields):
            raise ValueError("completed 贡献记录缺少统计字段")
        if self.from_ and self.to and self.from_ > self.to:
            raise ValueError("贡献时间区间无效")
        return self


class ImportFork(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=300)
    url: HttpUrl
    created_at: datetime
    pushed_at: datetime | None = None
    stars: int = Field(ge=0)
    owner: ImportOwner
    collected_at: datetime
    contribution: ImportContribution

    @field_validator("url")
    @classmethod
    def github_repository(cls, value: HttpUrl):
        if (value.scheme != "https" or value.host != "github.com" or value.username
                or value.password or value.query or value.fragment):
            raise ValueError("Fork URL 必须是 github.com HTTPS 地址")
        return value


class ImportTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_task_id: str = Field(min_length=1, max_length=100)
    repository_url: str
    canonical_url: HttpUrl | None = None
    source_status: Literal["queued", "running", "waiting_rate_limit", "completed", "partial", "failed", "cancelled"]
    from_: datetime = Field(alias="from")
    to: datetime
    total_direct_forks: int | None = Field(None, ge=0)
    truncated: bool
    limit: int = Field(ge=1, le=1000)
    forks_done: bool

    @field_validator("repository_url")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        return normalize_repository_url(value)

    @field_validator("canonical_url")
    @classmethod
    def validate_canonical(cls, value: HttpUrl | None):
        if value is not None and (value.scheme != "https" or value.host != "github.com"
                                  or value.username or value.password or value.query or value.fragment):
            raise ValueError("canonical_url 必须是 github.com HTTPS 地址")
        return value

    @model_validator(mode="after")
    def valid_interval(self):
        if self.from_ > self.to:
            raise ValueError("任务时间区间无效")
        return self


class ImportSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["classmates-explorer-snapshot"]
    version: Literal[1]
    exported_at: datetime
    task: ImportTask
    results: list[ImportFork] = Field(max_length=1000)

    @model_validator(mode="after")
    def consistent(self):
        ids = [row.id for row in self.results]
        if len(ids) != len(set(ids)):
            raise ValueError("包含重复的 Fork ID")
        if len(ids) > self.task.limit:
            raise ValueError("结果数超过任务上限")
        if self.task.total_direct_forks is not None and len(ids) > self.task.total_direct_forks:
            raise ValueError("结果数超过一级 Fork 总数")
        return self
