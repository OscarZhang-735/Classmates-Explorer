import hashlib
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_mode: Literal["remote", "local"] = "remote"
    session_secret: SecretStr = SecretStr("")
    allowed_origins: list[str] = []
    session_ttl_seconds: int = Field(180, ge=60)
    heartbeat_seconds: int = Field(30, ge=5)
    session_creations_per_minute: int = Field(5, ge=1)
    metrics_allowed_ips: list[str] = ["127.0.0.1", "::1"]
    max_sessions: int = Field(100, ge=1)
    max_sessions_per_credential: int = Field(10, ge=1)

    github_token: SecretStr = SecretStr("")
    unlimited_mode: bool = False
    database_url: str = "sqlite:///./remote.sqlite3"
    max_forks: int = Field(1000, ge=1, le=1000)
    max_import_bytes: int = Field(10 * 1024 * 1024, ge=1024)
    max_queued_tasks: int = Field(5, ge=1)
    submissions_per_minute: int = Field(5, ge=1)
    github_request_interval: float = Field(1, ge=0)
    contribution_batch_size: int = Field(10, ge=1, le=100)
    connect_timeout: float = Field(5, gt=0)
    request_timeout: float = Field(20, gt=0)
    max_retries: int = Field(3, ge=0, le=10)
    max_task_requests: int = Field(300, ge=1)
    max_task_points: int = Field(500, ge=1)
    rate_limit_reserve: int = Field(100, ge=0)
    max_consecutive_rate_limits: int = Field(5, ge=1)
    result_cache_seconds: int = Field(3600, ge=0)
    owner_cache_seconds: int = Field(86400, ge=0)
    contribution_cache_seconds: int = Field(21600, ge=0)

    @field_validator("allowed_origins")
    @classmethod
    def canonicalize_origins(cls, origins: list[str]) -> list[str]:
        # Browsers serialize origin hostnames in lowercase. Apply the same form
        # before both the access middleware and Starlette's exact CORS matching.
        # Keep paths/query/fragments intact so startup validation still rejects them.
        normalized = []
        for origin in origins:
            parsed = urlsplit(origin)
            value = parsed._replace(netloc=parsed.netloc.lower()).geturl()
            if value not in normalized:
                normalized.append(value)
        return normalized

    @property
    def credential_id(self) -> str:
        return hashlib.sha256(self.github_token.get_secret_value().encode()).hexdigest()
