from functools import lru_cache
import json
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = Field(default="Secure Agentic AI Work OS", validation_alias="APP_NAME")
    app_env: str = Field(default="development", validation_alias="APP_ENV")
    secret_key: str = Field(default="change-me", validation_alias="APP_SECRET_KEY")
    secret_key_file: str | None = Field(
        default=None, validation_alias="APP_SECRET_KEY_FILE"
    )
    encryption_keyring: str | None = Field(
        default=None, validation_alias="APP_ENCRYPTION_KEYRING"
    )
    encryption_keyring_file: str | None = Field(
        default=None, validation_alias="APP_ENCRYPTION_KEYRING_FILE"
    )
    active_encryption_key_id: str = Field(
        default="primary", validation_alias="APP_ACTIVE_ENCRYPTION_KEY_ID"
    )
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = Field(
        default="HS256", validation_alias="APP_JWT_ALGORITHM"
    )
    access_token_expire_minutes: int = Field(
        default=60, validation_alias="APP_ACCESS_TOKEN_EXPIRE_MINUTES"
    )
    refresh_token_expire_days: int = Field(
        default=14, validation_alias="APP_REFRESH_TOKEN_EXPIRE_DAYS"
    )
    oidc_redirect_base_url: str = Field(
        default="http://127.0.0.1:8000/api/auth/oidc",
        validation_alias="APP_OIDC_REDIRECT_BASE_URL",
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
    openclaw_mcp_internal_url: str = Field(
        default="http://backend:8000/protocol/mcp",
        validation_alias="APP_OPENCLAW_MCP_INTERNAL_URL",
    )
    openclaw_client_default_expiry_days: int = Field(
        default=30,
        ge=1,
        le=365,
        validation_alias="APP_OPENCLAW_CLIENT_DEFAULT_EXPIRY_DAYS",
    )
    openclaw_client_max_expiry_days: int = Field(
        default=365,
        ge=1,
        le=3650,
        validation_alias="APP_OPENCLAW_CLIENT_MAX_EXPIRY_DAYS",
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
    upload_max_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1_024,
        le=250 * 1024 * 1024,
        validation_alias="APP_UPLOAD_MAX_BYTES",
    )
    dlp_scan_max_bytes: int = Field(
        default=5 * 1024 * 1024,
        ge=1_024,
        le=50 * 1024 * 1024,
        validation_alias="APP_DLP_SCAN_MAX_BYTES",
    )
    malware_scanner_mode: Literal["disabled", "basic", "clamav"] = Field(
        default="basic", validation_alias="APP_MALWARE_SCANNER_MODE"
    )
    malware_fail_closed: bool = Field(
        default=False, validation_alias="APP_MALWARE_FAIL_CLOSED"
    )
    clamav_host: str = Field(default="127.0.0.1", validation_alias="CLAMAV_HOST")
    clamav_port: int = Field(default=3310, ge=1, le=65535, validation_alias="CLAMAV_PORT")
    clamav_timeout_seconds: float = Field(
        default=10.0,
        ge=0.5,
        le=120.0,
        validation_alias="CLAMAV_TIMEOUT_SECONDS",
    )
    rate_limit_enabled: bool = Field(
        default=True, validation_alias="APP_RATE_LIMIT_ENABLED"
    )
    rate_limit_backend: Literal["memory", "redis"] = Field(
        default="memory", validation_alias="APP_RATE_LIMIT_BACKEND"
    )
    rate_limit_requests: int = Field(
        default=120, ge=1, le=100_000, validation_alias="APP_RATE_LIMIT_REQUESTS"
    )
    rate_limit_auth_requests: int = Field(
        default=10, ge=1, le=10_000, validation_alias="APP_RATE_LIMIT_AUTH_REQUESTS"
    )
    rate_limit_upload_requests: int = Field(
        default=20, ge=1, le=10_000, validation_alias="APP_RATE_LIMIT_UPLOAD_REQUESTS"
    )
    rate_limit_window_seconds: int = Field(
        default=60,
        ge=1,
        le=3_600,
        validation_alias="APP_RATE_LIMIT_WINDOW_SECONDS",
    )
    security_headers_enabled: bool = Field(
        default=True, validation_alias="APP_SECURITY_HEADERS_ENABLED"
    )
    retention_batch_size: int = Field(
        default=1_000,
        ge=1,
        le=10_000,
        validation_alias="APP_RETENTION_BATCH_SIZE",
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
    model_provider: Literal["deterministic", "openai"] = Field(
        default="deterministic", validation_alias="APP_MODEL_PROVIDER"
    )
    openai_generation_model: str = Field(
        default="gpt-5.6", validation_alias="OPENAI_GENERATION_MODEL"
    )
    openai_generation_timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=120.0,
        validation_alias="OPENAI_GENERATION_TIMEOUT_SECONDS",
    )
    openai_generation_max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        validation_alias="OPENAI_GENERATION_MAX_RETRIES",
    )
    openai_generation_max_output_tokens: int = Field(
        default=1_200,
        ge=64,
        le=32_000,
        validation_alias="OPENAI_GENERATION_MAX_OUTPUT_TOKENS",
    )
    model_max_input_tokens: int = Field(
        default=16_000,
        ge=256,
        le=1_000_000,
        validation_alias="APP_MODEL_MAX_INPUT_TOKENS",
    )
    openai_generation_input_cost_per_million_tokens: float = Field(
        default=0.0,
        ge=0,
        validation_alias="OPENAI_GENERATION_INPUT_COST_PER_MILLION_TOKENS",
    )
    openai_generation_output_cost_per_million_tokens: float = Field(
        default=0.0,
        ge=0,
        validation_alias="OPENAI_GENERATION_OUTPUT_COST_PER_MILLION_TOKENS",
    )
    grounded_answers_enabled: bool = Field(
        default=True, validation_alias="APP_GROUNDED_ANSWERS_ENABLED"
    )
    llm_planner_enabled: bool = Field(
        default=False, validation_alias="APP_LLM_PLANNER_ENABLED"
    )
    llm_planner_max_actions: int = Field(
        default=8,
        ge=1,
        le=20,
        validation_alias="APP_LLM_PLANNER_MAX_ACTIONS",
    )
    default_daily_cost_limit_usd: float = Field(
        default=5.0,
        validation_alias="APP_DEFAULT_DAILY_COST_LIMIT_USD",
    )
    rag_evaluation_max_chunks: int = Field(
        default=500,
        ge=1,
        le=10_000,
        validation_alias="APP_RAG_EVALUATION_MAX_CHUNKS",
    )
    oauth_redirect_base_url: str = Field(
        default="http://127.0.0.1:8000/api/connectors",
        validation_alias="APP_OAUTH_REDIRECT_BASE_URL",
    )
    connector_webhook_base_url: str = Field(
        default="http://127.0.0.1:8000/api/connectors/webhooks",
        validation_alias="APP_CONNECTOR_WEBHOOK_BASE_URL",
    )
    connector_oauth_state_ttl_seconds: int = Field(
        default=600,
        ge=60,
        le=3600,
        validation_alias="APP_CONNECTOR_OAUTH_STATE_TTL_SECONDS",
    )
    connector_request_timeout_seconds: float = Field(
        default=20.0,
        ge=1.0,
        le=120.0,
        validation_alias="APP_CONNECTOR_REQUEST_TIMEOUT_SECONDS",
    )
    connector_sync_max_items: int = Field(
        default=100,
        ge=1,
        le=1000,
        validation_alias="APP_CONNECTOR_SYNC_MAX_ITEMS",
    )
    google_client_id: str | None = Field(default=None, validation_alias="GOOGLE_CLIENT_ID")
    google_client_secret: str | None = Field(
        default=None, validation_alias="GOOGLE_CLIENT_SECRET"
    )
    google_pubsub_service_account: str | None = Field(
        default=None, validation_alias="GOOGLE_PUBSUB_SERVICE_ACCOUNT"
    )
    google_pubsub_audience: str | None = Field(
        default=None, validation_alias="GOOGLE_PUBSUB_AUDIENCE"
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

    @model_validator(mode="after")
    def load_secret_files_and_validate_production(self) -> "Settings":
        if self.secret_key_file:
            self.secret_key = _read_secret_file(self.secret_key_file, "application secret key")
        if self.encryption_keyring_file:
            self.encryption_keyring = _read_secret_file(
                self.encryption_keyring_file, "encryption keyring"
            )
        parsed_keyring: dict[str, object] | None = None
        if self.encryption_keyring:
            try:
                candidate = json.loads(self.encryption_keyring)
            except json.JSONDecodeError as exc:
                raise ValueError("APP_ENCRYPTION_KEYRING must be a JSON object.") from exc
            if not isinstance(candidate, dict) or not candidate:
                raise ValueError("APP_ENCRYPTION_KEYRING must contain at least one key.")
            if self.active_encryption_key_id not in candidate:
                raise ValueError(
                    "APP_ACTIVE_ENCRYPTION_KEY_ID is absent from the encryption keyring."
                )
            parsed_keyring = candidate

        if self.app_env.lower() == "production":
            if self.secret_key == "change-me" or len(self.secret_key) < 32:
                raise ValueError(
                    "Production requires APP_SECRET_KEY or APP_SECRET_KEY_FILE "
                    "with at least 32 characters."
                )
            if not self.encryption_keyring:
                raise ValueError(
                    "Production requires APP_ENCRYPTION_KEYRING or "
                    "APP_ENCRYPTION_KEYRING_FILE."
                )
            if parsed_keyring is None or any(
                not isinstance(value, str) or len(value) < 32
                for value in parsed_keyring.values()
            ):
                raise ValueError(
                    "Every production encryption key must contain at least 32 characters."
                )
            if self.rate_limit_backend != "redis":
                raise ValueError("Production requires APP_RATE_LIMIT_BACKEND=redis.")
            if self.malware_scanner_mode == "disabled" or not self.malware_fail_closed:
                raise ValueError(
                    "Production requires malware scanning with APP_MALWARE_FAIL_CLOSED=true."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _read_secret_file(path_value: str, label: str) -> str:
    path = Path(path_value)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"Could not read {label} file: {path}") from exc
    if not value:
        raise ValueError(f"The {label} file is empty: {path}")
    return value
