from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = Field(default="Secure Agentic AI Work OS", validation_alias="APP_NAME")
    app_env: str = Field(default="development", validation_alias="APP_ENV")
    secret_key: str = Field(default="change-me", validation_alias="APP_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", validation_alias="APP_JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(
        default=60, validation_alias="APP_ACCESS_TOKEN_EXPIRE_MINUTES"
    )
    require_approval_for_send_email: bool = Field(
        default=True, validation_alias="APP_REQUIRE_APPROVAL_FOR_SEND_EMAIL"
    )
    require_approval_for_export: bool = Field(
        default=True, validation_alias="APP_REQUIRE_APPROVAL_FOR_EXPORT"
    )
    mcp_issuer_url: str = Field(
        default="http://127.0.0.1:8000",
        validation_alias="APP_MCP_ISSUER_URL",
    )
    mcp_server_url: str = Field(
        default="http://127.0.0.1:8000/protocol/mcp",
        validation_alias="APP_MCP_SERVER_URL",
    )
    database_path: str = Field(
        default=str(BASE_DIR / "data" / "workos.db"),
        validation_alias="APP_DATABASE_PATH",
    )
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    run_migrations_on_startup: bool = Field(
        default=True, validation_alias="APP_RUN_MIGRATIONS_ON_STARTUP"
    )
    upload_dir: str = Field(
        default=str(BASE_DIR / "data" / "uploads"),
        validation_alias="APP_UPLOAD_DIR",
    )
    async_jobs_enabled: bool = Field(
        default=False, validation_alias="APP_ASYNC_JOBS_ENABLED"
    )
    async_jobs_fallback_sync: bool = Field(
        default=True, validation_alias="APP_ASYNC_JOBS_FALLBACK_SYNC"
    )
    redis_url: str = Field(
        default="redis://127.0.0.1:6379/0", validation_alias="REDIS_URL"
    )
    job_queue_name: str = Field(
        default="ingestion", validation_alias="APP_JOB_QUEUE_NAME"
    )
    job_timeout_seconds: int = Field(
        default=600, validation_alias="APP_JOB_TIMEOUT_SECONDS"
    )
    vector_dimensions: int = Field(default=384, validation_alias="APP_VECTOR_DIMENSIONS")
    embedding_provider: str = Field(default="local", validation_alias="APP_EMBEDDING_PROVIDER")
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        validation_alias="OPENAI_EMBEDDING_MODEL",
    )
    openai_embedding_timeout_seconds: float = Field(
        default=20.0,
        validation_alias="OPENAI_EMBEDDING_TIMEOUT_SECONDS",
    )
    openai_embedding_cost_per_million_tokens: float = Field(
        default=0.0,
        validation_alias="OPENAI_EMBEDDING_COST_PER_MILLION_TOKENS",
    )
    default_daily_cost_limit_usd: float = Field(
        default=5.0,
        validation_alias="APP_DEFAULT_DAILY_COST_LIMIT_USD",
    )
    oauth_redirect_base_url: str = Field(
        default="http://127.0.0.1:8000/api/connectors",
        validation_alias="APP_OAUTH_REDIRECT_BASE_URL",
    )
    google_client_id: str | None = Field(default=None, validation_alias="GOOGLE_CLIENT_ID")
    google_client_secret: str | None = Field(
        default=None, validation_alias="GOOGLE_CLIENT_SECRET"
    )
    github_client_id: str | None = Field(default=None, validation_alias="GITHUB_CLIENT_ID")
    github_client_secret: str | None = Field(
        default=None, validation_alias="GITHUB_CLIENT_SECRET"
    )
    slack_client_id: str | None = Field(default=None, validation_alias="SLACK_CLIENT_ID")
    slack_client_secret: str | None = Field(default=None, validation_alias="SLACK_CLIENT_SECRET")
    notion_client_id: str | None = Field(default=None, validation_alias="NOTION_CLIENT_ID")
    notion_client_secret: str | None = Field(
        default=None, validation_alias="NOTION_CLIENT_SECRET"
    )
    jira_client_id: str | None = Field(default=None, validation_alias="JIRA_CLIENT_ID")
    jira_client_secret: str | None = Field(default=None, validation_alias="JIRA_CLIENT_SECRET")

    model_config = SettingsConfigDict(env_file=str(BASE_DIR.parent / ".env"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
