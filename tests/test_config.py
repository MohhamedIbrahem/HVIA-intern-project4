# tests/test_config.py
"""
Tests for config/settings.py and config/logging_config.py.
Covers: valid loading, defaults, password masking, cloud-model blocking,
        remote-host blocking, logging setup, PII sanitization, rotation,
        and exception capture via log_execution.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Settings Tests
# ---------------------------------------------------------------------------


class TestSettings:
    """Test Pydantic Settings loading, validation, and security guards."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        """Clear lru_cache between tests so env changes take effect."""
        from config.settings import get_settings
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def _make_settings(self, **overrides):
        """Create a Settings instance with test defaults + overrides.
        Uses _env_file=None to prevent Pydantic from reading the real .env.
        """
        from config.settings import Settings
        defaults = {
            "POSTGRES_USER": "test_user",
            "POSTGRES_PASSWORD": "test_pass",
            "POSTGRES_DB": "test_db",
            "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": "5432",
            "OLLAMA_BASE_URL": "http://localhost:11434",
            "OLLAMA_MODEL": "qwen3.5:4b",
            "LOG_LEVEL": "INFO",
        }
        defaults.update(overrides)
        with patch.dict(os.environ, defaults, clear=False):
            return Settings(_env_file=None, **defaults)

    def test_valid_settings_load(self):
        """Valid env vars load into Settings with expected types."""
        s = self._make_settings()
        assert s.postgres_user == "test_user"
        assert s.postgres_db == "test_db"
        assert s.postgres_port == 5432
        assert s.ollama_model == "qwen3.5:4b"
        assert s.log_level == "INFO"
        assert s.dq_fuzzy_match_threshold == 88
        assert s.dq_outlier_iqr_factor == 1.5

    def test_database_url_constructed(self):
        """DATABASE_URL is auto-constructed from parts."""
        s = self._make_settings()
        assert s.database_url is not None
        assert "test_user" in s.database_url
        assert "test_db" in s.database_url

    def test_async_database_url(self):
        """Async URL replaces postgresql:// with postgresql+asyncpg://."""
        s = self._make_settings()
        assert s.async_database_url.startswith("postgresql+asyncpg://")

    def test_password_masked_in_repr(self):
        """Password never appears in repr/str output."""
        s = self._make_settings()
        repr_str = repr(s)
        assert "test_pass" not in repr_str
        assert "***" in repr_str

    def test_password_masked_in_str(self):
        """Password never appears in str output."""
        s = self._make_settings()
        str_out = str(s)
        assert "test_pass" not in str_out
        assert "***" in str_out

    # --- Security: Cloud Model Blocking ---

    def test_block_cloud_model_kimi(self):
        """kimi-k3:cloud must be rejected."""
        with pytest.raises(Exception) as exc_info:
            self._make_settings(OLLAMA_MODEL="kimi-k3:cloud")
        assert "cloud" in str(exc_info.value).lower() or "SECURITY" in str(exc_info.value)

    def test_block_cloud_model_generic(self):
        """Any :cloud model must be rejected."""
        with pytest.raises(Exception):
            self._make_settings(OLLAMA_MODEL="llama3:cloud")

    def test_block_unlisted_model(self):
        """Models not in allowlist are rejected."""
        with pytest.raises(Exception):
            self._make_settings(OLLAMA_MODEL="gpt-4o")

    def test_allow_qwen_4b(self):
        """qwen3.5:4b is in the allowlist."""
        s = self._make_settings(OLLAMA_MODEL="qwen3.5:4b")
        assert s.ollama_model == "qwen3.5:4b"

    def test_allow_qwen_9b(self):
        """qwen3.5:9b is in the allowlist."""
        s = self._make_settings(OLLAMA_MODEL="qwen3.5:9b")
        assert s.ollama_model == "qwen3.5:9b"

    # --- Security: Remote Host Blocking ---

    def test_block_remote_ollama_url(self):
        """External Ollama URLs must be rejected."""
        with pytest.raises(Exception) as exc_info:
            self._make_settings(OLLAMA_BASE_URL="https://api.openai.com")
        assert "SECURITY" in str(exc_info.value) or "blocked" in str(exc_info.value).lower()

    def test_block_remote_ollama_custom_host(self):
        """Non-local hosts must be rejected."""
        with pytest.raises(Exception):
            self._make_settings(OLLAMA_BASE_URL="http://remote-server.com:11434")

    def test_allow_localhost(self):
        """localhost is allowed."""
        s = self._make_settings(OLLAMA_BASE_URL="http://localhost:11434")
        assert "localhost" in s.ollama_base_url

    def test_allow_127(self):
        """127.0.0.1 is allowed."""
        s = self._make_settings(OLLAMA_BASE_URL="http://127.0.0.1:11434")
        assert "127.0.0.1" in s.ollama_base_url

    def test_allow_docker_internal(self):
        """host.docker.internal is allowed."""
        s = self._make_settings(OLLAMA_BASE_URL="http://host.docker.internal:11434")
        assert "host.docker.internal" in s.ollama_base_url

    # --- Missing Required Fields ---

    def test_missing_password_raises(self):
        """Missing POSTGRES_PASSWORD raises ValidationError."""
        from config.settings import Settings
        # Remove POSTGRES_PASSWORD from env so it's truly missing
        env_without_pass = {k: v for k, v in os.environ.items() if k != "POSTGRES_PASSWORD"}
        with patch.dict(os.environ, env_without_pass, clear=True):
            with pytest.raises(Exception):
                Settings(
                    _env_file=None,
                    POSTGRES_USER="user",
                    POSTGRES_DB="db",
                    OLLAMA_MODEL="qwen3.5:4b",
                    OLLAMA_BASE_URL="http://localhost:11434",
                )


# ---------------------------------------------------------------------------
# Logging Tests
# ---------------------------------------------------------------------------


class TestLogging:
    """Test logging setup, PII sanitization, rotation, and exception capture."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from config.settings import get_settings
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    @pytest.fixture
    def log_dir(self, tmp_path):
        """Provide a temporary log directory."""
        return tmp_path / "test_logs"

    def _setup_with_env(self, log_dir: Path):
        """Setup logging with test env vars."""
        from config.logging_config import setup_logging
        env = {
            "POSTGRES_PASSWORD": "test_pass",
            "OLLAMA_MODEL": "qwen3.5:4b",
            "OLLAMA_BASE_URL": "http://localhost:11434",
        }
        with patch.dict(os.environ, env, clear=False):
            setup_logging(log_level="DEBUG", log_dir=log_dir)

    def test_get_logger_returns_named_logger(self, log_dir):
        """get_logger returns a logger with the given name."""
        self._setup_with_env(log_dir)
        from config.logging_config import get_logger
        logger = get_logger("etl.test")
        assert logger.name == "etl.test"

    def test_logger_writes_to_file(self, log_dir):
        """Logger writes formatted records to rotating log file."""
        self._setup_with_env(log_dir)
        from config.logging_config import get_logger
        logger = get_logger("test.file_write")
        logger.info("Test message for file output")

        log_file = log_dir / "hvia_pipeline.log"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "Test message for file output" in content
        assert "test.file_write" in content

    def test_pii_sanitizer_masks_db_url(self, log_dir):
        """Database URLs with passwords are masked in logs."""
        self._setup_with_env(log_dir)
        from config.logging_config import get_logger
        logger = get_logger("test.pii")
        logger.warning("Connecting to postgresql://postgres:supersecret@localhost:5432/db")

        log_file = log_dir / "hvia_pipeline.log"
        content = log_file.read_text(encoding="utf-8")
        assert "supersecret" not in content
        assert "***" in content

    def test_pii_sanitizer_masks_password(self, log_dir):
        """password= values are masked."""
        self._setup_with_env(log_dir)
        from config.logging_config import get_logger
        logger = get_logger("test.pii_pass")
        logger.info("password=my_super_secret_123")

        log_file = log_dir / "hvia_pipeline.log"
        content = log_file.read_text(encoding="utf-8")
        assert "my_super_secret_123" not in content

    def test_pii_sanitizer_masks_phone(self, log_dir):
        """Egyptian phone numbers are partially masked."""
        self._setup_with_env(log_dir)
        from config.logging_config import get_logger
        logger = get_logger("test.pii_phone")
        logger.info("Customer phone: 01012345678")

        log_file = log_dir / "hvia_pipeline.log"
        content = log_file.read_text(encoding="utf-8")
        assert "01012345678" not in content

    def test_pii_sanitizer_masks_email(self, log_dir):
        """Email addresses are masked."""
        self._setup_with_env(log_dir)
        from config.logging_config import get_logger
        logger = get_logger("test.pii_email")
        logger.info("User email: user@example.com")

        log_file = log_dir / "hvia_pipeline.log"
        content = log_file.read_text(encoding="utf-8")
        assert "user@example.com" not in content

    def test_pii_sanitizer_masks_disabled_hash(self, log_dir):
        """!DISABLED_NON_HASH! marker is redacted."""
        self._setup_with_env(log_dir)
        from config.logging_config import get_logger
        logger = get_logger("test.pii_hash")
        logger.info("Found: !DISABLED_NON_HASH!")

        log_file = log_dir / "hvia_pipeline.log"
        content = log_file.read_text(encoding="utf-8")
        assert "!DISABLED_NON_HASH!" not in content
        assert "REDACTED" in content

    def test_sanitize_function_directly(self):
        """_sanitize_text utility works standalone."""
        from config.logging_config import _sanitize_text
        result = _sanitize_text("postgresql://admin:secret123@db:5432/mydb")
        assert "secret123" not in result
        assert "***" in result

    def test_log_execution_decorator_success(self, log_dir):
        """log_execution logs entry/exit on success."""
        self._setup_with_env(log_dir)
        from config.logging_config import log_execution

        @log_execution("test.stage")
        def successful_func():
            return 42

        result = successful_func()
        assert result == 42

        log_file = log_dir / "hvia_pipeline.log"
        content = log_file.read_text(encoding="utf-8")
        assert "Starting: successful_func" in content
        assert "Completed: successful_func" in content

    def test_log_execution_decorator_failure(self, log_dir):
        """log_execution logs full traceback on exception and re-raises."""
        self._setup_with_env(log_dir)
        from config.logging_config import log_execution

        @log_execution("test.stage")
        def failing_func():
            raise ValueError("intentional test error")

        with pytest.raises(ValueError, match="intentional test error"):
            failing_func()

        log_file = log_dir / "hvia_pipeline.log"
        content = log_file.read_text(encoding="utf-8")
        assert "FAILED: failing_func" in content
        assert "intentional test error" in content

    def test_rotating_handler_rollover(self, tmp_path):
        """Rotating handler rolls over when maxBytes is exceeded."""
        log_dir = tmp_path / "rotation_test"
        from config.logging_config import setup_logging, get_logger
        env = {
            "POSTGRES_PASSWORD": "test_pass",
            "OLLAMA_MODEL": "qwen3.5:4b",
            "OLLAMA_BASE_URL": "http://localhost:11434",
        }
        with patch.dict(os.environ, env, clear=False):
            setup_logging(
                log_level="DEBUG",
                log_dir=log_dir,
                max_bytes=500,  # Tiny limit to force rollover
                backup_count=2,
            )

        logger = get_logger("test.rotation")
        # Write enough to trigger rollover
        for i in range(100):
            logger.debug("Rotation test message number %d with padding xxxxx", i)

        log_files = list(log_dir.glob("hvia_pipeline.log*"))
        assert len(log_files) >= 2, f"Expected rollover, got {len(log_files)} files"
