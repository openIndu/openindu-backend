"""Unit tests for application settings."""
import os
from unittest.mock import patch


class TestSettings:
    """Verify settings load correctly from environment and defaults."""

    def test_default_values(self):
        """Settings should have sensible defaults."""
        with patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://test:test@localhost:5432/testdb",
        }, clear=True):
            from app.core.config import Settings
            s = Settings(_env_file=None)  # type: ignore[call-arg]
            assert s.STORAGE_BACKEND == "local"
            assert s.JWT_ALGORITHM == "HS256"
            assert s.ACCESS_TOKEN_EXPIRE_MINUTES == 15
            assert s.REFRESH_TOKEN_EXPIRE_DAYS == 7
            assert s.WEB_PORT == 8004
            assert s.MCP_PORT == 8005
            assert s.SMS_MOCK_ENABLED is True
            assert s.SMS_MOCK_CODE == "888888"

    def test_env_override(self):
        """Environment variables should override defaults."""
        with patch.dict(os.environ, {
            "DATABASE_URL": "sqlite:///test.db",
            "STORAGE_BACKEND": "s3",
            "JWT_ALGORITHM": "RS256",
            "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
            "SMS_MOCK_ENABLED": "false",
        }, clear=True):
            from app.core.config import Settings
            s = Settings(_env_file=None)  # type: ignore[call-arg]
            assert s.STORAGE_BACKEND == "s3"
            assert s.JWT_ALGORITHM == "RS256"
            assert s.ACCESS_TOKEN_EXPIRE_MINUTES == 30
            assert s.SMS_MOCK_ENABLED is False

    def test_storage_defaults(self):
        """Storage-related settings have correct defaults."""
        with patch.dict(os.environ, {
            "DATABASE_URL": "sqlite:///test.db",
        }, clear=True):
            from app.core.config import Settings
            s = Settings(_env_file=None)  # type: ignore[call-arg]
            assert s.DATA_DIR == "/data/files"
            assert s.OSS_ENDPOINT == "http://localhost:9000"
            assert s.OSS_BUCKET == "openindu"
            assert s.OSS_REGION == "us-east-1"

    def test_limits(self):
        """Rate/size limit settings have reasonable defaults."""
        with patch.dict(os.environ, {
            "DATABASE_URL": "sqlite:///test.db",
        }, clear=True):
            from app.core.config import Settings
            s = Settings(_env_file=None)  # type: ignore[call-arg]
            assert s.DOWNLOAD_DAILY_LIMIT == 5
            assert s.PRESIGNED_URL_EXPIRE_MINUTES == 5
            assert s.DOCUMENT_MAX_SIZE_MB == 50
            assert s.SOFTWARE_MAX_SIZE_GB == 5
