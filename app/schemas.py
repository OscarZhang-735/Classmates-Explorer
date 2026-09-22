import re
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


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

