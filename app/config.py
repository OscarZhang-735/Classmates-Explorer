import hashlib

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    github_token: SecretStr = SecretStr("")
    unlimited_mode: bool = False
    database_url: str = "sqlite:///./explorer.sqlite3"
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

    @property
    def credential_id(self) -> str:
        return hashlib.sha256(self.github_token.get_secret_value().encode()).hexdigest()
