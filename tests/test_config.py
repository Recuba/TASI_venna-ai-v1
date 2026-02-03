"""
Tests for Configuration Module
"""

import pytest
import os
from unittest.mock import patch


class TestDatabaseConfig:
    """Test cases for DatabaseConfig."""

    def test_database_url_from_env(self):
        """Test that DATABASE_URL is read from environment."""
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"}):
            from config import DatabaseConfig
            # Need to reimport to get fresh instance
            config = DatabaseConfig()
            assert config.url == "postgresql://test:test@localhost/test"

    def test_database_url_required(self):
        """Test that missing DATABASE_URL raises error."""
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            from config import DatabaseConfig, ConfigurationError
            with pytest.raises(ConfigurationError) as exc_info:
                DatabaseConfig()
            assert "DATABASE_URL" in str(exc_info.value)

    def test_pool_size_default(self):
        """Test default pool size."""
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"}):
            from config import DatabaseConfig
            config = DatabaseConfig()
            assert config.pool_size == 5

    def test_pool_size_custom(self):
        """Test custom pool size from environment."""
        with patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://test:test@localhost/test",
            "DB_POOL_SIZE": "10"
        }):
            from config import DatabaseConfig
            config = DatabaseConfig()
            assert config.pool_size == 10


class TestLLMConfig:
    """Test cases for LLMConfig."""

    def test_api_key_from_env(self):
        """Test that API key is read from environment."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key-123"}):
            from config import LLMConfig
            config = LLMConfig()
            assert config.api_key == "test-key-123"

    def test_api_key_required(self):
        """Test that missing API key raises error."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=False):
            from config import LLMConfig, ConfigurationError
            with pytest.raises(ConfigurationError) as exc_info:
                LLMConfig()
            assert "OPENROUTER_API_KEY" in str(exc_info.value)

    def test_default_model(self):
        """Test default LLM model."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            from config import LLMConfig
            config = LLMConfig()
            assert config.model == "google/gemini-2.5-flash"

    def test_custom_model(self):
        """Test custom LLM model from environment."""
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-key",
            "LLM_MODEL": "anthropic/claude-3-opus"
        }):
            from config import LLMConfig
            config = LLMConfig()
            assert config.model == "anthropic/claude-3-opus"


class TestAppConfig:
    """Test cases for AppConfig."""

    def test_debug_default_false(self):
        """Test that debug defaults to False."""
        with patch.dict(os.environ, {}, clear=False):
            from config import AppConfig
            config = AppConfig()
            assert config.debug is False

    def test_debug_from_env(self):
        """Test debug mode from environment."""
        with patch.dict(os.environ, {"DEBUG": "true"}):
            from config import AppConfig
            config = AppConfig()
            assert config.debug is True

    def test_log_level_default(self):
        """Test default log level."""
        from config import AppConfig
        config = AppConfig()
        assert config.log_level == "INFO"

    def test_max_results_default(self):
        """Test default max results."""
        from config import AppConfig
        config = AppConfig()
        assert config.max_results == 100


class TestSecurityConfig:
    """Test cases for SecurityConfig."""

    def test_sql_validation_enabled_by_default(self):
        """Test that SQL validation is enabled by default."""
        from config import SecurityConfig
        config = SecurityConfig()
        assert config.enable_sql_validation is True

    def test_blocked_keywords_present(self):
        """Test that blocked SQL keywords are defined."""
        from config import SecurityConfig
        config = SecurityConfig()

        assert "DROP" in config.blocked_sql_keywords
        assert "DELETE" in config.blocked_sql_keywords
        assert "INSERT" in config.blocked_sql_keywords
        assert "UPDATE" in config.blocked_sql_keywords

    def test_allowed_tables_present(self):
        """Test that allowed tables are defined."""
        from config import SecurityConfig
        config = SecurityConfig()

        assert "company_financials" in config.allowed_tables
        assert "companies" in config.allowed_tables
        assert "sectors" in config.allowed_tables


class TestSettings:
    """Test cases for Settings singleton."""

    def test_settings_singleton(self):
        """Test that Settings is a singleton."""
        from config import Settings

        s1 = Settings()
        s2 = Settings()

        assert s1 is s2

    def test_get_settings_function(self):
        """Test get_settings convenience function."""
        from config import get_settings, Settings

        settings = get_settings()

        assert isinstance(settings, Settings)


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_get_database_url(self):
        """Test get_database_url function."""
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"}):
            # Need to reload to pick up new env var
            import importlib
            import config
            importlib.reload(config)

            from config import get_database_url
            url = get_database_url()

            assert "postgresql://" in url
