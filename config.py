"""
TASI Financial Database - Centralized Configuration
Secure configuration management with environment variable validation.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class ConfigurationError(Exception):
    """Raised when required configuration is missing or invalid."""
    pass


@dataclass
class DatabaseConfig:
    """PostgreSQL database configuration."""
    url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    pool_size: int = field(default_factory=lambda: int(os.getenv("DB_POOL_SIZE", "5")))
    max_overflow: int = field(default_factory=lambda: int(os.getenv("DB_MAX_OVERFLOW", "10")))
    query_timeout_seconds: int = field(default_factory=lambda: int(os.getenv("DB_QUERY_TIMEOUT", "30")))

    def __post_init__(self):
        if not self.url:
            raise ConfigurationError(
                "DATABASE_URL environment variable is required. "
                "Example: postgresql://user:password@localhost:5432/tasi_financials"
            )


@dataclass
class LLMConfig:
    """LLM service configuration (OpenRouter)."""
    api_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "google/gemini-2.5-flash"))
    base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1"))
    temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.1")))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "1000")))

    def __post_init__(self):
        if not self.api_key:
            raise ConfigurationError(
                "OPENROUTER_API_KEY environment variable is required. "
                "Get your key from: https://openrouter.ai/keys"
            )


@dataclass
class VannaConfig:
    """Vanna AI configuration."""
    api_key: str = field(default_factory=lambda: os.getenv("VANNA_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("VANNA_MODEL", "tasi-financials"))


@dataclass
class AppConfig:
    """Application-level configuration."""
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_file: Optional[str] = field(default_factory=lambda: os.getenv("LOG_FILE"))
    max_results: int = field(default_factory=lambda: int(os.getenv("MAX_RESULTS", "100")))
    cache_ttl_seconds: int = field(default_factory=lambda: int(os.getenv("CACHE_TTL", "300")))

    # Streamlit-specific
    page_title: str = "TASI Financial Database"
    page_icon: str = "chart_with_upwards_trend"
    layout: str = "wide"


@dataclass
class SecurityConfig:
    """Security-related configuration."""
    # SQL validation settings
    enable_sql_validation: bool = field(
        default_factory=lambda: os.getenv("ENABLE_SQL_VALIDATION", "true").lower() == "true"
    )
    blocked_sql_keywords: tuple = field(default_factory=lambda: (
        "DROP", "DELETE", "TRUNCATE", "ALTER", "CREATE", "INSERT", "UPDATE",
        "GRANT", "REVOKE", "EXEC", "EXECUTE", "COPY", "\\copy"
    ))
    allowed_tables: tuple = field(default_factory=lambda: (
        "company_financials", "companies", "sectors",
        "fiscal_periods", "financial_statements", "financial_metrics"
    ))
    max_query_rows: int = field(default_factory=lambda: int(os.getenv("MAX_QUERY_ROWS", "1000")))


class Settings:
    """
    Centralized settings manager.
    Lazily loads configuration to support testing with mocked env vars.
    """
    _instance = None
    _database: Optional[DatabaseConfig] = None
    _llm: Optional[LLMConfig] = None
    _vanna: Optional[VannaConfig] = None
    _app: Optional[AppConfig] = None
    _security: Optional[SecurityConfig] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def database(self) -> DatabaseConfig:
        if self._database is None:
            self._database = DatabaseConfig()
        return self._database

    @property
    def llm(self) -> LLMConfig:
        if self._llm is None:
            self._llm = LLMConfig()
        return self._llm

    @property
    def vanna(self) -> VannaConfig:
        if self._vanna is None:
            self._vanna = VannaConfig()
        return self._vanna

    @property
    def app(self) -> AppConfig:
        if self._app is None:
            self._app = AppConfig()
        return self._app

    @property
    def security(self) -> SecurityConfig:
        if self._security is None:
            self._security = SecurityConfig()
        return self._security

    def reload(self):
        """Reload all configuration from environment."""
        load_dotenv(override=True)
        self._database = None
        self._llm = None
        self._vanna = None
        self._app = None
        self._security = None

    def validate_all(self) -> bool:
        """
        Validate all required configuration is present.
        Returns True if valid, raises ConfigurationError if not.
        """
        # Access all configs to trigger validation
        _ = self.database
        _ = self.llm
        _ = self.app
        _ = self.security
        return True


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get the global settings instance."""
    return settings


# For backwards compatibility and ease of use
def get_database_url() -> str:
    """Get database URL from settings."""
    return settings.database.url


def get_llm_api_key() -> str:
    """Get LLM API key from settings."""
    return settings.llm.api_key
