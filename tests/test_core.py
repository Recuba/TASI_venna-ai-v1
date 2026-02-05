"""
Unit tests for TASI Financial Agent core components.
Tests SQL safety, query history, and data export -- no database or API required.
"""

import json
import pytest
import sys
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vanna_app import (
    validate_sql_safety,
    QueryHistory,
    export_results_csv,
    export_results_json,
)


# =============================================================================
# SQL Safety Guardrails
# =============================================================================

class TestSQLSafety:
    """Tests for the SQL safety validation layer."""

    def test_select_allowed(self):
        is_safe, _ = validate_sql_safety("SELECT * FROM company_financials LIMIT 10;")
        assert is_safe

    def test_select_with_where(self):
        is_safe, _ = validate_sql_safety(
            "SELECT ticker, company_name FROM company_financials WHERE is_latest = TRUE;"
        )
        assert is_safe

    def test_cte_allowed(self):
        sql = """
        WITH yearly AS (
            SELECT ticker, fiscal_year, revenue_millions
            FROM company_financials
            WHERE is_annual = TRUE
        )
        SELECT * FROM yearly ORDER BY revenue_millions DESC LIMIT 10;
        """
        is_safe, _ = validate_sql_safety(sql)
        assert is_safe

    def test_explain_allowed(self):
        is_safe, _ = validate_sql_safety("EXPLAIN SELECT * FROM company_financials;")
        assert is_safe

    def test_drop_table_blocked(self):
        is_safe, reason = validate_sql_safety("DROP TABLE companies;")
        assert not is_safe
        assert "Blocked" in reason

    def test_delete_blocked(self):
        is_safe, reason = validate_sql_safety("DELETE FROM financial_statements WHERE company_id = 1;")
        assert not is_safe

    def test_insert_blocked(self):
        is_safe, reason = validate_sql_safety("INSERT INTO companies (ticker) VALUES ('TEST');")
        assert not is_safe

    def test_update_blocked(self):
        is_safe, reason = validate_sql_safety("UPDATE companies SET company_name = 'hack' WHERE ticker = '1010';")
        assert not is_safe

    def test_truncate_blocked(self):
        is_safe, reason = validate_sql_safety("TRUNCATE TABLE financial_statements;")
        assert not is_safe

    def test_alter_blocked(self):
        is_safe, reason = validate_sql_safety("ALTER TABLE companies ADD COLUMN hack TEXT;")
        assert not is_safe

    def test_create_table_blocked(self):
        is_safe, reason = validate_sql_safety("CREATE TABLE hack (id INT);")
        assert not is_safe

    def test_grant_blocked(self):
        is_safe, reason = validate_sql_safety("GRANT ALL ON companies TO public;")
        assert not is_safe

    def test_multi_statement_blocked(self):
        is_safe, reason = validate_sql_safety("SELECT 1; DROP TABLE companies;")
        assert not is_safe

    def test_empty_query_blocked(self):
        is_safe, reason = validate_sql_safety("")
        assert not is_safe

    def test_whitespace_only_blocked(self):
        is_safe, reason = validate_sql_safety("   ")
        assert not is_safe

    def test_non_select_start_blocked(self):
        is_safe, reason = validate_sql_safety("COPY companies TO '/tmp/data.csv';")
        assert not is_safe

    def test_select_with_subquery(self):
        sql = """
        SELECT ticker, company_name,
               (SELECT AVG(roe_percent) FROM company_financials) as avg_roe
        FROM company_financials
        WHERE is_latest = TRUE;
        """
        is_safe, _ = validate_sql_safety(sql)
        assert is_safe


# =============================================================================
# Query History
# =============================================================================

class TestQueryHistory:
    """Tests for the query history tracking component."""

    def test_empty_history(self):
        history = QueryHistory()
        assert len(history.entries) == 0
        assert history.to_json() == "[]"
        assert history.to_csv() == ""

    def test_log_success(self):
        history = QueryHistory()
        history.log("test question", "SELECT 1", 1, True, duration_ms=50.0)
        assert len(history.entries) == 1
        assert history.entries[0]["success"] is True
        assert history.entries[0]["row_count"] == 1

    def test_log_failure(self):
        history = QueryHistory()
        history.log("bad query", "SELECT bad", 0, False, error="relation not found", duration_ms=10.0)
        assert len(history.entries) == 1
        assert history.entries[0]["success"] is False
        assert history.entries[0]["error"] == "relation not found"

    def test_multiple_entries(self):
        history = QueryHistory()
        for i in range(5):
            history.log(f"question {i}", f"SELECT {i}", i, True, duration_ms=float(i))
        assert len(history.entries) == 5

    def test_to_json(self):
        history = QueryHistory()
        history.log("test", "SELECT 1", 1, True, duration_ms=10.0)
        data = json.loads(history.to_json())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["question"] == "test"

    def test_to_csv(self):
        history = QueryHistory()
        history.log("test", "SELECT 1", 1, True, duration_ms=10.0)
        csv_str = history.to_csv()
        assert "question" in csv_str
        assert "test" in csv_str


# =============================================================================
# Data Export
# =============================================================================

class TestDataExport:
    """Tests for CSV/JSON result export functions."""

    SAMPLE_RESULTS = [
        {"ticker": "1010", "company_name": "RIBL", "roe_percent": 18.5},
        {"ticker": "2222", "company_name": "Saudi Aramco", "roe_percent": 25.3},
    ]

    def test_csv_export(self):
        csv_str = export_results_csv(self.SAMPLE_RESULTS)
        assert "ticker" in csv_str
        assert "1010" in csv_str
        assert "Saudi Aramco" in csv_str
        # Should have header + 2 data rows
        lines = csv_str.strip().split("\n")
        assert len(lines) == 3

    def test_csv_empty(self):
        assert export_results_csv([]) == ""

    def test_json_export(self):
        json_str = export_results_json(self.SAMPLE_RESULTS)
        data = json.loads(json_str)
        assert len(data) == 2
        assert data[0]["ticker"] == "1010"
        assert data[1]["roe_percent"] == 25.3

    def test_json_empty(self):
        json_str = export_results_json([])
        assert json.loads(json_str) == []

    def test_json_handles_dates(self):
        from datetime import date, datetime
        results = [{"date": date(2024, 1, 15), "ts": datetime(2024, 6, 30, 12, 0)}]
        json_str = export_results_json(results)
        data = json.loads(json_str)
        assert data[0]["date"] == "2024-01-15"
        assert "2024-06-30" in data[0]["ts"]
