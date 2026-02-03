"""
Pytest Configuration and Fixtures
"""

import os
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def setup_test_env():
    """Set up test environment variables."""
    test_env = {
        "DATABASE_URL": "postgresql://test:test@localhost:5432/test_db",
        "OPENROUTER_API_KEY": "test-api-key-12345",
        "DEBUG": "false",
        "LOG_LEVEL": "WARNING",  # Reduce log noise during tests
    }

    with patch.dict(os.environ, test_env):
        yield


@pytest.fixture
def mock_db_connection():
    """Mock database connection for tests."""
    from unittest.mock import MagicMock

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    return mock_conn, mock_cursor


@pytest.fixture
def sample_financial_data():
    """Sample financial data for tests."""
    return [
        {
            "ticker": "1010",
            "company_name": "Test Company A",
            "sector": "Banking",
            "roe_percent": 15.5,
            "net_profit_millions": 1000.0,
            "revenue_millions": 5000.0,
            "is_latest": True,
            "is_annual": True,
        },
        {
            "ticker": "2020",
            "company_name": "Test Company B",
            "sector": "Insurance",
            "roe_percent": 12.3,
            "net_profit_millions": 500.0,
            "revenue_millions": 3000.0,
            "is_latest": True,
            "is_annual": True,
        },
        {
            "ticker": "3030",
            "company_name": "Test Company C",
            "sector": "Real Estate",
            "roe_percent": -5.0,
            "net_profit_millions": -100.0,
            "revenue_millions": 1000.0,
            "is_latest": True,
            "is_annual": True,
        },
    ]


@pytest.fixture
def sample_sql_queries():
    """Sample SQL queries for validation tests."""
    return {
        "valid_select": "SELECT * FROM company_financials WHERE is_latest = TRUE LIMIT 10",
        "valid_join": "SELECT c.ticker, s.sector_name FROM companies c JOIN sectors s ON c.sector_id = s.sector_id LIMIT 10",
        "invalid_drop": "DROP TABLE companies",
        "invalid_delete": "DELETE FROM companies WHERE ticker = '1234'",
        "invalid_insert": "INSERT INTO companies (ticker) VALUES ('test')",
        "injection_union": "SELECT * FROM companies WHERE ticker = '1' UNION SELECT * FROM pg_catalog.pg_tables",
        "injection_chained": "SELECT * FROM companies; DROP TABLE companies",
    }
