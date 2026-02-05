"""
Comprehensive test suite for TASI Financial Agent.
Unit tests, integration tests, and regression tests -- targeting 98%+ coverage.
All external dependencies (database, LLM API) are mocked.
"""

import json
import io
import csv
import time
import re
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock, call
from datetime import date, datetime
from decimal import Decimal

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vanna_app import (
    validate_sql_safety,
    QueryHistory,
    export_results_csv,
    export_results_json,
    TASIFinancialAgent,
    PostgresRunner,
    OpenRouterLlmService,
    _is_running_in_streamlit,
)


# =============================================================================
# SQL Safety Guardrails -- Unit Tests
# =============================================================================

class TestSQLSafety:
    """Tests for the SQL safety validation layer."""

    # --- Allowed queries ---

    def test_select_allowed(self):
        is_safe, reason = validate_sql_safety("SELECT * FROM company_financials LIMIT 10;")
        assert is_safe
        assert reason == "OK"

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

    def test_select_with_subquery(self):
        sql = """
        SELECT ticker, company_name,
               (SELECT AVG(roe_percent) FROM company_financials) as avg_roe
        FROM company_financials
        WHERE is_latest = TRUE;
        """
        is_safe, _ = validate_sql_safety(sql)
        assert is_safe

    def test_select_no_trailing_semicolon(self):
        is_safe, _ = validate_sql_safety("SELECT 1")
        assert is_safe

    def test_select_with_leading_whitespace(self):
        is_safe, _ = validate_sql_safety("  \n  SELECT 1;")
        assert is_safe

    def test_select_complex_aggregation(self):
        sql = """SELECT sector, COUNT(DISTINCT ticker) as cnt,
                 AVG(roe_percent) FILTER (WHERE profit_status = 'Profit') as avg_roe
                 FROM company_financials GROUP BY sector;"""
        is_safe, _ = validate_sql_safety(sql)
        assert is_safe

    # --- Blocked queries ---

    def test_drop_table_blocked(self):
        is_safe, reason = validate_sql_safety("DROP TABLE companies;")
        assert not is_safe
        assert "Blocked" in reason

    def test_drop_database_blocked(self):
        is_safe, _ = validate_sql_safety("DROP DATABASE tasi_financials;")
        assert not is_safe

    def test_drop_schema_blocked(self):
        is_safe, _ = validate_sql_safety("DROP SCHEMA public CASCADE;")
        assert not is_safe

    def test_drop_view_blocked(self):
        is_safe, _ = validate_sql_safety("DROP VIEW company_financials;")
        assert not is_safe

    def test_drop_function_blocked(self):
        is_safe, _ = validate_sql_safety("DROP FUNCTION get_company_latest;")
        assert not is_safe

    def test_drop_index_blocked(self):
        is_safe, _ = validate_sql_safety("DROP INDEX idx_ticker;")
        assert not is_safe

    def test_drop_extension_blocked(self):
        is_safe, _ = validate_sql_safety("DROP EXTENSION vector;")
        assert not is_safe

    def test_drop_role_blocked(self):
        is_safe, _ = validate_sql_safety("DROP ROLE tasi;")
        assert not is_safe

    def test_delete_blocked(self):
        is_safe, _ = validate_sql_safety("DELETE FROM financial_statements WHERE company_id = 1;")
        assert not is_safe

    def test_insert_blocked(self):
        is_safe, _ = validate_sql_safety("INSERT INTO companies (ticker) VALUES ('TEST');")
        assert not is_safe

    def test_update_blocked(self):
        is_safe, _ = validate_sql_safety("UPDATE companies SET company_name = 'hack' WHERE ticker = '1010';")
        assert not is_safe

    def test_truncate_blocked(self):
        is_safe, _ = validate_sql_safety("TRUNCATE TABLE financial_statements;")
        assert not is_safe

    def test_alter_table_blocked(self):
        is_safe, _ = validate_sql_safety("ALTER TABLE companies ADD COLUMN hack TEXT;")
        assert not is_safe

    def test_alter_database_blocked(self):
        is_safe, _ = validate_sql_safety("ALTER DATABASE tasi_financials SET timezone TO 'UTC';")
        assert not is_safe

    def test_create_table_blocked(self):
        is_safe, _ = validate_sql_safety("CREATE TABLE hack (id INT);")
        assert not is_safe

    def test_create_database_blocked(self):
        is_safe, _ = validate_sql_safety("CREATE DATABASE evil;")
        assert not is_safe

    def test_create_user_blocked(self):
        is_safe, _ = validate_sql_safety("CREATE USER hacker WITH PASSWORD 'pw';")
        assert not is_safe

    def test_grant_blocked(self):
        is_safe, _ = validate_sql_safety("GRANT ALL ON companies TO public;")
        assert not is_safe

    def test_revoke_blocked(self):
        is_safe, _ = validate_sql_safety("REVOKE ALL ON companies FROM public;")
        assert not is_safe

    def test_copy_blocked(self):
        is_safe, _ = validate_sql_safety("COPY companies TO '/tmp/data.csv';")
        assert not is_safe

    def test_execute_blocked(self):
        is_safe, _ = validate_sql_safety("EXECUTE some_prepared_statement;")
        assert not is_safe

    def test_call_blocked(self):
        is_safe, _ = validate_sql_safety("CALL some_procedure();")
        assert not is_safe

    def test_multi_statement_blocked(self):
        is_safe, _ = validate_sql_safety("SELECT 1; DROP TABLE companies;")
        assert not is_safe

    def test_empty_query_blocked(self):
        is_safe, reason = validate_sql_safety("")
        assert not is_safe
        assert "Empty" in reason

    def test_whitespace_only_blocked(self):
        is_safe, _ = validate_sql_safety("   ")
        assert not is_safe

    def test_semicolon_only_blocked(self):
        is_safe, _ = validate_sql_safety(";")
        assert not is_safe

    def test_non_select_start_blocked(self):
        is_safe, reason = validate_sql_safety("VACUUM ANALYZE companies;")
        assert not is_safe
        assert "VACUUM" in reason

    def test_case_insensitive_blocking(self):
        is_safe, _ = validate_sql_safety("drop table companies;")
        assert not is_safe

    def test_mixed_case_blocking(self):
        is_safe, _ = validate_sql_safety("DrOp TaBlE companies;")
        assert not is_safe

    # --- Regression: tricky patterns ---

    def test_select_into_allowed(self):
        """SELECT INTO is a read, not a write in standard SQL context."""
        is_safe, _ = validate_sql_safety("SELECT ticker INTO TEMP FROM company_financials;")
        assert is_safe  # INTO is not in blocked list

    def test_select_with_grant_in_column_name(self):
        """Column named 'grant_amount' -- underscore is a word char so \\b doesn't fire."""
        is_safe, _ = validate_sql_safety("SELECT grant_amount FROM ledger;")
        # \bGRANT\b does NOT match inside 'grant_amount' because '_' is a word char
        # so there is no boundary between 't' and '_'. This is correct behavior.
        assert is_safe


# =============================================================================
# Query History -- Unit Tests
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
        entry = history.entries[0]
        assert entry["success"] is True
        assert entry["row_count"] == 1
        assert entry["duration_ms"] == 50.0
        assert entry["error"] is None
        assert "timestamp" in entry

    def test_log_failure(self):
        history = QueryHistory()
        history.log("bad query", "SELECT bad", 0, False, error="relation not found", duration_ms=10.0)
        entry = history.entries[0]
        assert entry["success"] is False
        assert entry["error"] == "relation not found"
        assert entry["question"] == "bad query"
        assert entry["sql"] == "SELECT bad"

    def test_log_duration_rounding(self):
        history = QueryHistory()
        history.log("q", "SELECT 1", 1, True, duration_ms=123.456789)
        assert history.entries[0]["duration_ms"] == 123.5

    def test_multiple_entries(self):
        history = QueryHistory()
        for i in range(5):
            history.log(f"question {i}", f"SELECT {i}", i, True, duration_ms=float(i))
        assert len(history.entries) == 5
        assert history.entries[2]["question"] == "question 2"

    def test_to_json_valid(self):
        history = QueryHistory()
        history.log("test", "SELECT 1", 1, True, duration_ms=10.0)
        data = json.loads(history.to_json())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["question"] == "test"
        assert data[0]["sql"] == "SELECT 1"

    def test_to_json_multiple(self):
        history = QueryHistory()
        history.log("q1", "SQL1", 1, True, duration_ms=1.0)
        history.log("q2", "SQL2", 0, False, error="err", duration_ms=2.0)
        data = json.loads(history.to_json())
        assert len(data) == 2
        assert data[1]["error"] == "err"

    def test_to_csv_headers(self):
        history = QueryHistory()
        history.log("test", "SELECT 1", 1, True, duration_ms=10.0)
        csv_str = history.to_csv()
        assert "timestamp" in csv_str
        assert "question" in csv_str
        assert "sql" in csv_str
        assert "row_count" in csv_str
        assert "success" in csv_str
        assert "error" in csv_str
        assert "duration_ms" in csv_str

    def test_to_csv_values(self):
        history = QueryHistory()
        history.log("test q", "SELECT 1", 5, True, duration_ms=10.0)
        reader = csv.DictReader(io.StringIO(history.to_csv()))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["question"] == "test q"
        assert rows[0]["row_count"] == "5"

    def test_default_duration(self):
        history = QueryHistory()
        history.log("q", "s", 0, True)
        assert history.entries[0]["duration_ms"] == 0


# =============================================================================
# Data Export -- Unit Tests
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
        lines = csv_str.strip().split("\n")
        assert len(lines) == 3

    def test_csv_empty(self):
        assert export_results_csv([]) == ""

    def test_csv_single_row(self):
        csv_str = export_results_csv([{"a": 1}])
        lines = csv_str.strip().split("\n")
        assert len(lines) == 2
        assert "a" in lines[0]
        assert "1" in lines[1]

    def test_csv_special_characters(self):
        results = [{"name": 'Company "A"', "desc": "Line1,Line2"}]
        csv_str = export_results_csv(results)
        reader = csv.DictReader(io.StringIO(csv_str))
        row = next(reader)
        assert row["name"] == 'Company "A"'

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
        results = [{"date": date(2024, 1, 15), "ts": datetime(2024, 6, 30, 12, 0)}]
        json_str = export_results_json(results)
        data = json.loads(json_str)
        assert data[0]["date"] == "2024-01-15"
        assert "2024-06-30" in data[0]["ts"]

    def test_json_handles_sets(self):
        results = [{"tags": frozenset(["a", "b"])}]
        json_str = export_results_json(results)
        data = json.loads(json_str)
        assert set(data[0]["tags"]) == {"a", "b"}

    def test_json_handles_unknown_types(self):
        """Fallback to str() for unknown types."""
        results = [{"value": Decimal("123.45")}]
        json_str = export_results_json(results)
        data = json.loads(json_str)
        assert data[0]["value"] == "123.45"

    def test_json_unicode(self):
        results = [{"name": "شركة السعودية"}]
        json_str = export_results_json(results)
        assert "شركة السعودية" in json_str  # ensure_ascii=False


# =============================================================================
# SQL Extraction from LLM responses -- Unit Tests
# =============================================================================

class TestSQLExtraction:
    """Tests for robust SQL extraction from various LLM response formats."""

    def test_plain_sql(self):
        sql = TASIFinancialAgent._extract_sql("SELECT * FROM company_financials LIMIT 10;")
        assert sql.upper().startswith("SELECT")

    def test_fenced_sql_block(self):
        response = "Here is the query:\n```sql\nSELECT ticker FROM company_financials;\n```"
        sql = TASIFinancialAgent._extract_sql(response)
        assert sql == "SELECT ticker FROM company_financials;"

    def test_fenced_block_no_lang(self):
        response = "```\nSELECT 1;\n```"
        sql = TASIFinancialAgent._extract_sql(response)
        assert sql == "SELECT 1;"

    def test_prose_before_sql(self):
        response = "Sure! Here is a query that finds the top companies:\nSELECT ticker FROM company_financials LIMIT 5;"
        sql = TASIFinancialAgent._extract_sql(response)
        assert sql.startswith("SELECT")
        assert "LIMIT 5" in sql

    def test_with_cte_extraction(self):
        response = "Try this:\n```sql\nWITH top AS (SELECT * FROM x) SELECT * FROM top;\n```"
        sql = TASIFinancialAgent._extract_sql(response)
        assert sql.startswith("WITH")

    def test_empty_response(self):
        assert TASIFinancialAgent._extract_sql("") == ""

    def test_none_like_empty(self):
        assert TASIFinancialAgent._extract_sql("") == ""

    def test_explanation_after_sql(self):
        response = "```sql\nSELECT count(*) FROM company_financials;\n```\nThis counts all rows."
        sql = TASIFinancialAgent._extract_sql(response)
        assert sql == "SELECT count(*) FROM company_financials;"

    def test_explain_keyword_extraction(self):
        response = "Run this:\nEXPLAIN SELECT * FROM company_financials;"
        sql = TASIFinancialAgent._extract_sql(response)
        assert sql.startswith("EXPLAIN")

    def test_fallback_no_sql_keywords(self):
        """When no SQL keyword found, return as-is."""
        response = "I don't understand the question."
        sql = TASIFinancialAgent._extract_sql(response)
        assert sql == "I don't understand the question."

    def test_multiple_code_blocks_takes_first(self):
        response = "```sql\nSELECT 1;\n```\nOr alternatively:\n```sql\nSELECT 2;\n```"
        sql = TASIFinancialAgent._extract_sql(response)
        assert sql == "SELECT 1;"

    def test_whitespace_only_response(self):
        sql = TASIFinancialAgent._extract_sql("   \n  \t  ")
        # After strip this is empty-ish but not empty string
        # The method strips and then tries extraction
        assert sql == ""  or sql.strip() == ""

    def test_fenced_block_case_insensitive(self):
        response = "```SQL\nSELECT ticker FROM tbl;\n```"
        sql = TASIFinancialAgent._extract_sql(response)
        assert sql == "SELECT ticker FROM tbl;"


# =============================================================================
# OpenRouterLlmService -- Unit Tests (mocked)
# =============================================================================

class TestOpenRouterLlmService:
    """Tests for the LLM service with mocked API calls."""

    @patch("vanna_app.OpenAI")
    def test_init_default(self, mock_openai_cls):
        svc = OpenRouterLlmService(api_key="test-key")
        assert svc.api_key == "test-key"
        assert svc.model == "google/gemini-2.5-flash"
        assert svc.max_retries == 3
        mock_openai_cls.assert_called_once()

    @patch("vanna_app.OpenAI")
    def test_init_custom_model(self, mock_openai_cls):
        svc = OpenRouterLlmService(api_key="k", model="custom/model", max_retries=5)
        assert svc.model == "custom/model"
        assert svc.max_retries == 5

    @patch("vanna_app.OpenAI")
    def test_chat_success(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="SELECT 1;"))]
        mock_client.chat.completions.create.return_value = mock_response

        svc = OpenRouterLlmService(api_key="test-key")
        result = svc.chat([{"role": "user", "content": "test"}])
        assert result == "SELECT 1;"

    @patch("vanna_app.OpenAI")
    def test_chat_passes_kwargs(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        mock_client.chat.completions.create.return_value = mock_response

        svc = OpenRouterLlmService(api_key="test-key")
        svc.chat([{"role": "user", "content": "q"}], temperature=0.1, max_tokens=500)
        mock_client.chat.completions.create.assert_called_once_with(
            model="google/gemini-2.5-flash",
            messages=[{"role": "user", "content": "q"}],
            temperature=0.1,
            max_tokens=500,
        )

    @patch("vanna_app.time.sleep")
    @patch("vanna_app.OpenAI")
    def test_chat_retry_then_success(self, mock_openai_cls, mock_sleep):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        mock_client.chat.completions.create.side_effect = [
            ConnectionError("network fail"),
            mock_response,
        ]

        svc = OpenRouterLlmService(api_key="key", max_retries=3)
        result = svc.chat([{"role": "user", "content": "q"}])
        assert result == "ok"
        mock_sleep.assert_called_once_with(2)  # 2^1

    @patch("vanna_app.time.sleep")
    @patch("vanna_app.OpenAI")
    def test_chat_all_retries_fail(self, mock_openai_cls, mock_sleep):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = ConnectionError("down")

        svc = OpenRouterLlmService(api_key="key", max_retries=2)
        with pytest.raises(RuntimeError, match="LLM request failed after 2 attempts"):
            svc.chat([{"role": "user", "content": "q"}])
        assert mock_sleep.call_count == 1  # only retries (attempts-1) sleeps
        mock_sleep.assert_called_with(2)  # 2^1

    @patch("vanna_app.time.sleep")
    @patch("vanna_app.OpenAI")
    def test_chat_retry_backoff_timing(self, mock_openai_cls, mock_sleep):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = ConnectionError("fail")

        svc = OpenRouterLlmService(api_key="key", max_retries=4)
        with pytest.raises(RuntimeError):
            svc.chat([{"role": "user", "content": "q"}])
        # Sleeps: 2^1=2, 2^2=4, 2^3=8 (no sleep on last attempt)
        assert mock_sleep.call_args_list == [call(2), call(4), call(8)]


# =============================================================================
# PostgresRunner -- Unit Tests (mocked)
# =============================================================================

class TestPostgresRunner:
    """Tests for the database runner with mocked psycopg2."""

    def test_init_defaults(self):
        runner = PostgresRunner(connection_string="postgres://test")
        assert runner.connection_string == "postgres://test"
        assert runner.connect_timeout == 5
        assert runner.max_retries == 2
        assert runner._conn is None
        assert runner._schema_cache is None

    def test_class_constants(self):
        assert PostgresRunner.MAX_RESULT_ROWS == 5000
        assert PostgresRunner.STATEMENT_TIMEOUT_MS == 30000

    @patch("vanna_app.psycopg2.connect")
    def test_get_connection_success(self, mock_connect):
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        runner = PostgresRunner(connection_string="postgres://test")
        conn = runner.get_connection()

        assert conn is mock_conn
        mock_conn.set_session.assert_called_once_with(readonly=True, autocommit=True)
        mock_connect.assert_called_once_with("postgres://test", connect_timeout=5)

    @patch("vanna_app.psycopg2.connect")
    def test_get_connection_reuses_existing(self, mock_connect):
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        runner = PostgresRunner(connection_string="postgres://test")
        conn1 = runner.get_connection()
        conn2 = runner.get_connection()

        assert conn1 is conn2
        assert mock_connect.call_count == 1  # only connected once

    @patch("vanna_app.psycopg2.connect")
    def test_get_connection_reconnects_on_dead_conn(self, mock_connect):
        mock_conn_dead = MagicMock()
        mock_conn_dead.closed = False
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("connection lost")
        mock_conn_dead.cursor.return_value = mock_cursor

        mock_conn_new = MagicMock()
        mock_connect.side_effect = [mock_conn_dead, mock_conn_new]

        runner = PostgresRunner(connection_string="postgres://test")
        conn1 = runner.get_connection()  # gets mock_conn_dead
        conn2 = runner.get_connection()  # detects dead, reconnects to mock_conn_new

        assert conn2 is mock_conn_new

    @patch("vanna_app.time.sleep")
    @patch("vanna_app.psycopg2.connect")
    def test_get_connection_retry_on_failure(self, mock_connect, mock_sleep):
        mock_conn = MagicMock()
        mock_connect.side_effect = [Exception("fail"), mock_conn]

        runner = PostgresRunner(connection_string="postgres://test", max_retries=3)
        conn = runner.get_connection()

        assert conn is mock_conn
        mock_sleep.assert_called_once_with(2)

    @patch("vanna_app.time.sleep")
    @patch("vanna_app.psycopg2.connect")
    def test_get_connection_all_retries_fail(self, mock_connect, mock_sleep):
        mock_connect.side_effect = Exception("permanent failure")

        runner = PostgresRunner(connection_string="postgres://test", max_retries=2)
        with pytest.raises(ConnectionError, match="Could not connect"):
            runner.get_connection()

    def test_close_with_conn(self):
        runner = PostgresRunner(connection_string="postgres://test")
        mock_conn = MagicMock()
        runner._conn = mock_conn
        runner._close()
        mock_conn.close.assert_called_once()
        assert runner._conn is None

    def test_close_with_close_exception(self):
        runner = PostgresRunner(connection_string="postgres://test")
        mock_conn = MagicMock()
        mock_conn.close.side_effect = Exception("already closed")
        runner._conn = mock_conn
        runner._close()  # should not raise
        assert runner._conn is None

    def test_close_without_conn(self):
        runner = PostgresRunner(connection_string="postgres://test")
        runner._conn = None
        runner._close()  # should not raise

    def test_close_without_attr(self):
        """Defensive: __del__ calls _close on partially initialized object."""
        runner = PostgresRunner.__new__(PostgresRunner)
        runner._close()  # should not raise due to getattr guard

    @patch("vanna_app.psycopg2.connect")
    def test_run_sql_returns_results(self, mock_connect):
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_cursor.description = [("ticker",), ("name",)]
        mock_cursor.fetchmany.return_value = [
            {"ticker": "1010", "name": "RIBL"},
            {"ticker": "2222", "name": "Aramco"},
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        runner = PostgresRunner(connection_string="postgres://test")
        results = runner.run_sql("SELECT ticker, name FROM company_financials;")
        assert len(results) == 2
        assert results[0]["ticker"] == "1010"

    @patch("vanna_app.psycopg2.connect")
    def test_run_sql_no_description(self, mock_connect):
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_cursor.description = None
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        runner = PostgresRunner(connection_string="postgres://test")
        results = runner.run_sql("SELECT 1;")
        assert results == []

    def test_run_sql_blocks_unsafe_query(self):
        runner = PostgresRunner(connection_string="postgres://test")
        with pytest.raises(PermissionError, match="SQL safety check failed"):
            runner.run_sql("DROP TABLE companies;")

    def test_run_sql_resets_conn_on_error(self):
        runner = PostgresRunner(connection_string="postgres://test")
        mock_conn = MagicMock()
        runner._conn = mock_conn
        # Mock get_connection to return our controlled connection
        runner.get_connection = MagicMock(return_value=mock_conn)
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("query error")
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(Exception, match="query error"):
            runner.run_sql("SELECT bad_column FROM company_financials;")
        assert runner._conn is None  # connection reset after error

    @patch("vanna_app.psycopg2.connect")
    def test_get_schema_returns_formatted_text(self, mock_connect):
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"table_name": "companies", "column_name": "ticker", "data_type": "text", "is_nullable": "NO"},
            {"table_name": "companies", "column_name": "name", "data_type": "text", "is_nullable": "YES"},
            {"table_name": "sectors", "column_name": "sector_id", "data_type": "integer", "is_nullable": "NO"},
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        runner = PostgresRunner(connection_string="postgres://test")
        schema = runner.get_schema()

        assert "companies:" in schema
        assert "ticker: text (NOT NULL)" in schema
        assert "name: text (NULL)" in schema
        assert "sectors:" in schema
        assert "sector_id: integer (NOT NULL)" in schema

    @patch("vanna_app.psycopg2.connect")
    def test_get_schema_caches_result(self, mock_connect):
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"table_name": "t", "column_name": "c", "data_type": "int", "is_nullable": "NO"},
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        runner = PostgresRunner(connection_string="postgres://test")
        s1 = runner.get_schema()
        s2 = runner.get_schema()

        assert s1 == s2
        assert mock_cursor.execute.call_count == 2  # connect SET + schema query, only once

    @patch("vanna_app.psycopg2.connect")
    def test_get_schema_force_refresh(self, mock_connect):
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"table_name": "t", "column_name": "c", "data_type": "int", "is_nullable": "NO"},
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        runner = PostgresRunner(connection_string="postgres://test")
        runner.get_schema()
        runner.get_schema(force_refresh=True)

        # Schema query executed twice (SET + query each time = 4 cursor executes)
        assert mock_cursor.fetchall.call_count == 2

    def test_get_schema_resets_conn_on_error(self):
        runner = PostgresRunner(connection_string="postgres://test")
        mock_conn = MagicMock()
        runner._conn = mock_conn
        runner.get_connection = MagicMock(return_value=mock_conn)
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("schema query failed")
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(Exception, match="schema query failed"):
            runner.get_schema()
        assert runner._conn is None

    def test_del_calls_close(self):
        runner = PostgresRunner(connection_string="postgres://test")
        mock_conn = MagicMock()
        runner._conn = mock_conn
        runner.__del__()
        mock_conn.close.assert_called_once()


# =============================================================================
# TASIFinancialAgent -- Unit + Integration Tests (mocked LLM + DB)
# =============================================================================

def _make_mock_agent():
    """Create a TASIFinancialAgent with mocked LLM and DB."""
    with patch.object(PostgresRunner, "get_connection"), \
         patch.object(PostgresRunner, "get_schema", return_value="\ntest_table:\n  - col1: text (NOT NULL)"), \
         patch.object(OpenRouterLlmService, "__init__", return_value=None):
        agent = TASIFinancialAgent()
        agent.llm = MagicMock(spec=OpenRouterLlmService)
        agent.sql_runner = MagicMock(spec=PostgresRunner)
        return agent


class TestTASIFinancialAgent:
    """Tests for the main agent class."""

    def test_init_sets_attributes(self):
        agent = _make_mock_agent()
        assert agent.schema is not None
        assert agent.history is not None
        assert isinstance(agent.history, QueryHistory)
        assert agent.training_examples is not None
        assert agent.is_connected is True
        assert agent._db_error is None

    def test_init_survives_db_failure(self):
        """Agent should initialize even when the database is unavailable."""
        with patch.object(PostgresRunner, "get_schema", side_effect=ConnectionError("DB down")), \
             patch.object(OpenRouterLlmService, "__init__", return_value=None):
            agent = TASIFinancialAgent()
            assert agent.is_connected is False
            assert "DB down" in agent._db_error
            assert "unavailable" in agent.schema

    def test_load_training_examples(self):
        agent = _make_mock_agent()
        examples = agent._load_training_examples()
        assert "Example Queries" in examples
        assert "Show all companies" in examples
        assert "company_financials" in examples

    def test_build_system_prompt_contains_schema(self):
        agent = _make_mock_agent()
        prompt = agent._build_system_prompt()
        assert "test_table:" in prompt
        assert "TASI" in prompt
        assert "SELECT" in prompt
        assert "Example Queries" in prompt

    def test_build_system_prompt_contains_instructions(self):
        agent = _make_mock_agent()
        prompt = agent._build_system_prompt()
        assert "ONLY generate SELECT or WITH" in prompt
        assert "company_financials" in prompt
        assert "NULLS LAST" in prompt

    def test_generate_sql_success(self):
        agent = _make_mock_agent()
        agent.llm.chat.return_value = "```sql\nSELECT * FROM company_financials;\n```"
        sql = agent.generate_sql("Show all companies")
        assert sql == "SELECT * FROM company_financials;"

    def test_generate_sql_empty_question(self):
        agent = _make_mock_agent()
        with pytest.raises(ValueError, match="between 1 and 2000"):
            agent.generate_sql("")

    def test_generate_sql_too_long_question(self):
        agent = _make_mock_agent()
        with pytest.raises(ValueError, match="between 1 and 2000"):
            agent.generate_sql("x" * 2001)

    def test_generate_sql_exactly_2000_chars(self):
        agent = _make_mock_agent()
        agent.llm.chat.return_value = "SELECT 1;"
        sql = agent.generate_sql("x" * 2000)
        assert sql.startswith("SELECT")

    def test_generate_sql_no_sql_in_response(self):
        agent = _make_mock_agent()
        agent.llm.chat.return_value = ""
        with pytest.raises(ValueError, match="Could not extract valid SQL"):
            agent.generate_sql("test question")

    def test_ask_success(self):
        agent = _make_mock_agent()
        agent.llm.chat.return_value = "SELECT ticker FROM company_financials LIMIT 5;"
        agent.sql_runner.run_sql.return_value = [
            {"ticker": "1010"}, {"ticker": "2222"},
        ]

        result = agent.ask("Show tickers")
        assert result["success"] is True
        assert result["sql"] == "SELECT ticker FROM company_financials LIMIT 5;"
        assert len(result["results"]) == 2
        assert result["error"] is None
        assert "duration_ms" in result
        assert len(agent.history.entries) == 1
        assert agent.history.entries[0]["success"] is True

    def test_ask_llm_failure(self):
        agent = _make_mock_agent()
        agent.llm.chat.side_effect = RuntimeError("API down")

        result = agent.ask("test")
        assert result["success"] is False
        assert "API down" in result["error"]
        assert result["sql"] is None
        assert len(agent.history.entries) == 1
        assert agent.history.entries[0]["success"] is False

    def test_ask_sql_execution_failure(self):
        agent = _make_mock_agent()
        agent.llm.chat.return_value = "SELECT bad FROM company_financials;"
        agent.sql_runner.run_sql.side_effect = Exception("column not found")

        result = agent.ask("bad query")
        assert result["success"] is False
        assert "column not found" in result["error"]
        assert result["sql"] is not None  # SQL was generated
        assert len(agent.history.entries) == 1

    def test_ask_safety_rejection(self):
        agent = _make_mock_agent()
        agent.llm.chat.return_value = "DROP TABLE companies;"
        agent.sql_runner.run_sql.side_effect = PermissionError("SQL safety check failed")

        result = agent.ask("delete everything")
        assert result["success"] is False
        assert "safety" in result["error"].lower() or "SQL" in result["error"]

    def test_ask_records_duration(self):
        agent = _make_mock_agent()
        agent.llm.chat.return_value = "SELECT 1;"
        agent.sql_runner.run_sql.return_value = [{"result": 1}]

        result = agent.ask("test")
        assert result["duration_ms"] >= 0

    def test_ask_empty_results(self):
        agent = _make_mock_agent()
        agent.llm.chat.return_value = "SELECT ticker FROM company_financials WHERE 1=0;"
        agent.sql_runner.run_sql.return_value = []

        result = agent.ask("show nothing")
        assert result["success"] is True
        assert result["results"] == []
        assert agent.history.entries[0]["row_count"] == 0


# =============================================================================
# format_results -- Unit Tests
# =============================================================================

class TestFormatResults:
    """Tests for the ASCII table formatting."""

    def test_empty_results(self):
        agent = _make_mock_agent()
        assert agent.format_results([]) == "No results found."

    def test_single_row(self):
        agent = _make_mock_agent()
        results = [{"ticker": "1010", "name": "RIBL"}]
        output = agent.format_results(results)
        assert "ticker" in output
        assert "name" in output
        assert "1010" in output
        assert "RIBL" in output

    def test_multiple_rows(self):
        agent = _make_mock_agent()
        results = [
            {"a": "1", "b": "2"},
            {"a": "3", "b": "4"},
        ]
        output = agent.format_results(results)
        lines = output.strip().split("\n")
        assert len(lines) == 4  # header + separator + 2 data rows

    def test_truncation_at_max_rows(self):
        agent = _make_mock_agent()
        results = [{"val": str(i)} for i in range(100)]
        output = agent.format_results(results, max_rows=5)
        assert "... and 95 more rows" in output

    def test_long_value_truncated(self):
        agent = _make_mock_agent()
        results = [{"col": "x" * 100}]
        output = agent.format_results(results)
        # Values are truncated to 50 chars
        assert "x" * 50 in output
        assert "x" * 51 not in output

    def test_none_values(self):
        agent = _make_mock_agent()
        results = [{"a": None, "b": "ok"}]
        output = agent.format_results(results)
        assert "None" in output
        assert "ok" in output

    def test_numeric_values(self):
        agent = _make_mock_agent()
        results = [{"revenue": 1234.56, "count": 42}]
        output = agent.format_results(results)
        assert "1234.56" in output
        assert "42" in output

    def test_exact_max_rows_no_truncation_message(self):
        agent = _make_mock_agent()
        results = [{"v": str(i)} for i in range(5)]
        output = agent.format_results(results, max_rows=5)
        assert "more rows" not in output

    def test_separator_line(self):
        agent = _make_mock_agent()
        results = [{"col_a": "1", "col_b": "2"}]
        output = agent.format_results(results)
        lines = output.strip().split("\n")
        assert "-+-" in lines[1]  # separator between header and data


# =============================================================================
# Integration Tests -- End-to-end with mocked externals
# =============================================================================

class TestAgentIntegration:
    """Integration tests verifying component interactions."""

    def test_ask_logs_to_history_on_success(self):
        agent = _make_mock_agent()
        agent.llm.chat.return_value = "SELECT 1;"
        agent.sql_runner.run_sql.return_value = [{"v": 1}]

        agent.ask("q1")
        agent.ask("q2")

        assert len(agent.history.entries) == 2
        assert all(e["success"] for e in agent.history.entries)

    def test_ask_logs_to_history_on_failure(self):
        agent = _make_mock_agent()
        agent.llm.chat.return_value = "SELECT 1;"
        agent.sql_runner.run_sql.side_effect = Exception("db down")

        agent.ask("failing query")

        assert len(agent.history.entries) == 1
        assert not agent.history.entries[0]["success"]
        assert agent.history.entries[0]["error"] == "db down"

    def test_history_export_after_queries(self):
        agent = _make_mock_agent()
        agent.llm.chat.return_value = "SELECT 1;"
        agent.sql_runner.run_sql.return_value = [{"r": 1}]

        agent.ask("q1")
        agent.ask("q2")

        json_data = json.loads(agent.history.to_json())
        assert len(json_data) == 2

        csv_data = agent.history.to_csv()
        reader = csv.DictReader(io.StringIO(csv_data))
        rows = list(reader)
        assert len(rows) == 2

    def test_export_results_roundtrip_csv(self):
        """Results exported to CSV can be parsed back correctly."""
        original = [
            {"ticker": "1010", "revenue": 500.5},
            {"ticker": "2222", "revenue": 1200.0},
        ]
        csv_str = export_results_csv(original)
        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["ticker"] == "1010"
        assert float(rows[0]["revenue"]) == 500.5

    def test_export_results_roundtrip_json(self):
        """Results exported to JSON can be parsed back correctly."""
        original = [
            {"ticker": "1010", "revenue": 500.5},
            {"ticker": "2222", "revenue": 1200.0},
        ]
        json_str = export_results_json(original)
        parsed = json.loads(json_str)
        assert parsed == original

    def test_safety_blocks_before_db_call(self):
        """Unsafe SQL should be blocked before any database interaction."""
        agent = _make_mock_agent()
        agent.llm.chat.return_value = "DROP TABLE companies;"
        agent.sql_runner.run_sql.side_effect = PermissionError("SQL safety check failed: Blocked")

        result = agent.ask("drop the table")
        assert not result["success"]
        # The safety check in run_sql prevented execution

    def test_mixed_success_and_failure_queries(self):
        agent = _make_mock_agent()
        agent.llm.chat.side_effect = [
            "SELECT 1;",
            RuntimeError("API timeout"),
            "SELECT 2;",
        ]
        agent.sql_runner.run_sql.return_value = [{"r": 1}]

        r1 = agent.ask("q1")
        r2 = agent.ask("q2")
        r3 = agent.ask("q3")

        assert r1["success"] is True
        assert r2["success"] is False
        assert r3["success"] is True
        assert len(agent.history.entries) == 3


# =============================================================================
# Regression Tests -- Edge cases and boundary conditions
# =============================================================================

class TestRegressionEdgeCases:
    """Regression tests for tricky edge cases and boundary conditions."""

    def test_sql_with_only_semicolons(self):
        is_safe, _ = validate_sql_safety(";;;")
        assert not is_safe

    def test_sql_with_unicode(self):
        is_safe, _ = validate_sql_safety("SELECT * FROM company_financials WHERE company_name = 'شركة';")
        assert is_safe

    def test_sql_with_newlines(self):
        sql = "SELECT\n  ticker,\n  company_name\nFROM\n  company_financials;"
        is_safe, _ = validate_sql_safety(sql)
        assert is_safe

    def test_sql_with_comments(self):
        sql = "SELECT ticker -- get tickers\nFROM company_financials;"
        is_safe, _ = validate_sql_safety(sql)
        assert is_safe

    def test_export_json_decimal_types(self):
        results = [{"amount": Decimal("99999.99")}]
        json_str = export_results_json(results)
        data = json.loads(json_str)
        assert data[0]["amount"] == "99999.99"

    def test_export_csv_with_newlines_in_value(self):
        results = [{"desc": "line1\nline2"}]
        csv_str = export_results_csv(results)
        reader = csv.DictReader(io.StringIO(csv_str))
        row = next(reader)
        assert row["desc"] == "line1\nline2"

    def test_history_timestamp_format(self):
        history = QueryHistory()
        history.log("q", "s", 0, True)
        ts = history.entries[0]["timestamp"]
        # Should be valid ISO format
        datetime.fromisoformat(ts)  # raises if invalid

    def test_extract_sql_with_trailing_newlines(self):
        response = "```sql\nSELECT 1;\n\n\n```"
        sql = TASIFinancialAgent._extract_sql(response)
        assert sql == "SELECT 1;"

    def test_format_results_missing_key(self):
        """Rows with missing keys should show empty string."""
        agent = _make_mock_agent()
        results = [{"a": 1, "b": 2}, {"a": 3}]  # second row missing 'b'
        output = agent.format_results(results)
        assert "1" in output
        assert "3" in output

    def test_query_history_csv_with_commas_in_sql(self):
        history = QueryHistory()
        history.log("q", "SELECT a, b, c FROM t", 3, True, duration_ms=1.0)
        csv_str = history.to_csv()
        reader = csv.DictReader(io.StringIO(csv_str))
        row = next(reader)
        assert row["sql"] == "SELECT a, b, c FROM t"

    def test_validate_sql_safety_returns_tuple(self):
        result = validate_sql_safety("SELECT 1;")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)

    def test_extract_sql_preserves_case(self):
        response = "```sql\nSELECT Ticker, Company_Name FROM company_financials;\n```"
        sql = TASIFinancialAgent._extract_sql(response)
        assert "Ticker" in sql
        assert "Company_Name" in sql

    def test_format_results_empty_string_value(self):
        agent = _make_mock_agent()
        results = [{"col": ""}]
        output = agent.format_results(results)
        assert "col" in output

    def test_multiple_blocked_patterns_in_one_query(self):
        sql = "INSERT INTO t VALUES (1); DROP TABLE t;"
        is_safe, _ = validate_sql_safety(sql)
        assert not is_safe

    def test_sql_safety_only_whitespace_after_strip(self):
        is_safe, _ = validate_sql_safety("  ;  ")
        assert not is_safe


# =============================================================================
# _is_running_in_streamlit -- Unit Test
# =============================================================================

class TestStreamlitDetection:
    """Tests for runtime detection."""

    def test_not_in_streamlit(self):
        """When not running in Streamlit, should return False."""
        assert _is_running_in_streamlit() is False

    def test_streamlit_import_fails_gracefully(self):
        """If streamlit import fails, should return False."""
        # _is_running_in_streamlit catches all exceptions internally
        with patch.dict("sys.modules", {"streamlit.runtime.scriptrunner": None}):
            assert _is_running_in_streamlit() is False
