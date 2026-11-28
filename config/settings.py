# config/settings.py
"""
Typed application settings loaded from .env via Pydantic BaseSettings.
Includes Ollama cloud-model blocker, SecretStr credential masking, and DSN validation.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Project root is two levels up from config/settings.py → HVIA-intern-project4/
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Allowed Ollama models — ONLY local models, NEVER :cloud
# ---------------------------------------------------------------------------
_ALLOWED_OLLAMA_MODELS: set[str] = {
    "qwen3.5:4b",
    "qwen3.5:9b",
}

_LOCAL_HOSTS: set[str] = {
    "localhost",
    "127.0.0.1",
    "::1",
    "host.docker.internal",
}

_DSN_PASSWORD_RE = re.compile(r"(postgresql(?:\+\w+)?://[^:/@]+:)[^@]+(@)")


def _mask_dsn(url: str | None) -> str | None:
    """Return a DSN string with any embedded password replaced by '***'."""
    if not url:
        return url
    return _DSN_PASSWORD_RE.sub(r"\1***\2", url)


class Settings(BaseSettings):
    """Application-wide configuration validated at startup."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- PostgreSQL Database ---
    postgres_user: str = "hvia_admin"
    postgres_password: SecretStr
    postgres_db: str = "amira_training_lab"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    database_url: str | None = None

    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- Ollama (Local AI — Strictly Local Only) ---
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:4b"
    ollama_timeout_seconds: int = 30
    ollama_max_retries: int = 2

    # --- Application Settings ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_dir: str = "logs"

    # --- Data Quality Thresholds ---
    dq_fuzzy_match_threshold: int = 88
    dq_outlier_iqr_factor: float = 1.5

    # -----------------------------------------------------------------------
    # Computed / Validated Fields
    # -----------------------------------------------------------------------
    @model_validator(mode="after")
    def _build_database_url(self) -> Settings:
        """Construct or validate DATABASE_URL using psycopg2 driver."""
        if self.database_url:
            if "${" in self.database_url:
                raise ValueError(
                    "DATABASE_URL contains unexpanded '${...}' template syntax. "
                    "pydantic-settings does not expand shell variables inside .env values; "
                    "remove DATABASE_URL to let Settings construct it or set a literal DSN."
                )
            if not self.database_url.startswith(("postgresql://", "postgresql+psycopg2://")):
                raise ValueError(
                    "DATABASE_URL must use a 'postgresql://' or 'postgresql+psycopg2://' scheme."
                )
            if self.database_url.startswith("postgresql://"):
                self.database_url = self.database_url.replace(
                    "postgresql://", "postgresql+psycopg2://", 1
                )
        else:
            pwd = self.postgres_password.get_secret_value()
            self.database_url = (
                f"postgresql+psycopg2://{self.postgres_user}:{pwd}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return self

    @field_validator("ollama_model")
    @classmethod
    def _block_cloud_models(cls, v: str) -> str:
        """Hard-block any :cloud or external model."""
        if ":cloud" in v.lower():
            raise ValueError(
                f"SECURITY VIOLATION: Cloud model '{v}' is blocked. "
                f"Only local models are allowed: {_ALLOWED_OLLAMA_MODELS}"
            )
        if v not in _ALLOWED_OLLAMA_MODELS:
            raise ValueError(
                f"Model '{v}' is not in the allowlist. "
                f"Allowed models: {_ALLOWED_OLLAMA_MODELS}"
            )
        return v

    @field_validator("ollama_base_url")
    @classmethod
    def _block_remote_ollama(cls, v: str) -> str:
        """Ensure Ollama URL points to a loopback/local host only."""
        match = re.match(r"https?://(?:\[([^\]]+)\]|([^:/]+))", v)
        if not match:
            raise ValueError(f"Invalid Ollama URL: '{v}'")
        host = (match.group(1) or match.group(2)).lower()
        if host not in _LOCAL_HOSTS:
            raise ValueError(
                f"SECURITY VIOLATION: Remote Ollama host '{host}' is blocked. "
                f"Ollama must run locally. Allowed hosts: {_LOCAL_HOSTS}"
            )
        return v

    @property
    def safe_database_url(self) -> str:
        """Return DATABASE_URL with password masked."""
        return _mask_dsn(self.database_url) or ""

    @property
    def async_database_url(self) -> str:
        """Return asyncpg-compatible DATABASE_URL."""
        if self.database_url:
            url = self.database_url
            for prefix in ("postgresql+psycopg2://", "postgresql://"):
                if url.startswith(prefix):
                    return url.replace(prefix, "postgresql+asyncpg://", 1)
        return ""

    @property
    def log_path(self) -> Path:
        """Absolute path to log directory."""
        p = PROJECT_ROOT / self.log_dir
        p.mkdir(parents=True, exist_ok=True)
        return p

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Override model_dump so credentials are always masked."""
        data = super().model_dump(*args, **kwargs)
        data["postgres_password"] = "***"
        if data.get("database_url"):
            data["database_url"] = _mask_dsn(data["database_url"])
        return data

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        """Override model_dump_json so credentials are always masked in JSON."""
        import json

        kwargs.setdefault("exclude", set())
        # Serialize via safe model_dump dict for consistent masking
        data = self.model_dump(mode="json")
        return json.dumps(data)

    def __repr__(self) -> str:
        """Mask password in repr output."""
        return (
            f"Settings(db={self.postgres_host}:{self.postgres_port}/{self.postgres_db}, "
            f"user={self.postgres_user}, password=***, "
            f"ollama={self.ollama_base_url} model={self.ollama_model}, "
            f"log_level={self.log_level})"
        )

    def __str__(self) -> str:
        return self.__repr__()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton Settings instance."""
    return Settings()
