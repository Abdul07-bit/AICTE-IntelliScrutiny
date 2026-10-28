"""Runtime configuration. All secrets come from the environment; nothing is hardcoded."""
from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_name: str = "AICTE IntelliScrutiny"
    # demo | development | production. Demo/synthetic data and the guided demo are
    # only available when app_env != production.
    app_env: str = "demo"
    api_prefix: str = "/api/v1"

    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'intelliscrutiny.db').as_posix()}"
    storage_dir: Path = BACKEND_DIR / "data" / "storage"
    # Optional Fernet key. When set, uploaded files are encrypted at rest.
    storage_encryption_key: str | None = None

    jwt_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    jwt_ttl_minutes: int = 480
    signed_url_ttl_seconds: int = 300
    cookie_secure: bool = False
    cors_origins: list[str] = ["http://localhost:3000"]

    max_upload_mb: int = 20
    allowed_upload_types: list[str] = ["application/pdf", "image/png", "image/jpeg"]

    rate_limit_per_minute: int = 240
    login_rate_limit_per_minute: int = 10

    # Embeddings: "hashing" (local, deterministic, lexical), "sentence-transformers", or "http".
    embedding_provider: str = "hashing"
    embedding_model: str = "local-hashing-v1"
    embedding_dim: int = 384
    embedding_http_url: str | None = None

    # Optional LLM explainer. When unset, findings are explained by a deterministic,
    # template-grounded explainer (no model call is claimed).
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"

    ocr_enabled: bool = True
    tesseract_cmd: str | None = None
    clamav_host: str | None = None
    clamav_port: int = 3310

    job_node_timeout_seconds: int = 120
    job_node_max_retries: int = 2

    seed_demo_data: bool = True
    demo_password: str = "Demo@12345"

    @field_validator("cors_origins", "allowed_upload_types", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    def assert_safe_for_environment(self) -> None:
        if self.is_production:
            if self.seed_demo_data:
                raise RuntimeError("SEED_DEMO_DATA must be false in production")
            if len(self.jwt_secret) < 32:
                raise RuntimeError("JWT_SECRET must be set to a strong value in production")
            if not self.cookie_secure:
                raise RuntimeError("COOKIE_SECURE must be true in production")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.storage_dir.mkdir(parents=True, exist_ok=True)
    if s.database_url.startswith("sqlite:///"):
        Path(s.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return s
