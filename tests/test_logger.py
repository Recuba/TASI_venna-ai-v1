"""
Tests for Logger Module
"""

import pytest
import logging
from unittest.mock import MagicMock, patch

from logger import (
    TASILogger, get_logger, log_execution_time,
    QueryMetrics, query_metrics, JSONFormatter, ConsoleFormatter
)


class TestTASILogger:
    """Test cases for TASILogger class."""

    def test_logger_initialization(self):
        """Test logger initializes correctly."""
        logger = TASILogger("test_logger")

        assert logger.logger.name == "test_logger"
        assert len(logger.logger.handlers) >= 1

    def test_logger_with_file(self, tmp_path):
        """Test logger with file output."""
        log_file = tmp_path / "test.log"
        logger = TASILogger("test_file_logger", log_file=str(log_file))

        logger.info("Test message")

        # File should be created
        assert log_file.exists()

    def test_log_levels(self, capsys):
        """Test different log levels."""
        logger = TASILogger("test_levels", level="DEBUG")

        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")

        captured = capsys.readouterr()

        assert "Debug message" in captured.out or "Debug message" in captured.err
        assert "Info message" in captured.out or "Info message" in captured.err

    def test_log_query(self, capsys):
        """Test query logging."""
        logger = TASILogger("test_query")

        logger.log_query(
            question="Test question",
            sql="SELECT * FROM test",
            execution_time_ms=100.5,
            row_count=10,
            success=True
        )

        captured = capsys.readouterr()
        assert "100.5" in captured.out or "100.50" in captured.out

    def test_log_query_with_error(self, capsys):
        """Test query logging with error."""
        logger = TASILogger("test_query_error")

        logger.log_query(
            question="Test question",
            sql="SELECT * FROM test",
            execution_time_ms=50.0,
            row_count=0,
            success=False,
            error="Connection failed"
        )

        captured = capsys.readouterr()
        assert "failed" in captured.out.lower() or "error" in captured.out.lower()


class TestGetLogger:
    """Test cases for get_logger function."""

    def test_get_logger_creates_instance(self):
        """Test that get_logger creates a logger instance."""
        logger = get_logger("test_get_logger")

        assert isinstance(logger, TASILogger)

    def test_get_logger_returns_same_instance(self):
        """Test that get_logger returns the same instance for same name."""
        logger1 = get_logger("test_same")
        logger2 = get_logger("test_same")

        assert logger1 is logger2

    def test_get_logger_different_names(self):
        """Test that different names create different loggers."""
        logger1 = get_logger("test_diff_1")
        logger2 = get_logger("test_diff_2")

        assert logger1 is not logger2


class TestLogExecutionTime:
    """Test cases for log_execution_time decorator."""

    def test_decorator_logs_success(self):
        """Test that decorator logs successful execution."""
        @log_execution_time("test_decorator")
        def sample_function():
            return "result"

        result = sample_function()

        assert result == "result"

    def test_decorator_logs_exception(self):
        """Test that decorator logs exceptions."""
        @log_execution_time("test_decorator_error")
        def failing_function():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            failing_function()

    def test_decorator_measures_time(self):
        """Test that decorator measures execution time."""
        import time

        @log_execution_time("test_time")
        def slow_function():
            time.sleep(0.1)
            return True

        result = slow_function()

        assert result is True


class TestQueryMetrics:
    """Test cases for QueryMetrics class."""

    @pytest.fixture
    def metrics(self):
        """Create a fresh QueryMetrics instance."""
        return QueryMetrics()

    def test_initial_state(self, metrics):
        """Test initial metrics state."""
        assert metrics.total_queries == 0
        assert metrics.successful_queries == 0
        assert metrics.failed_queries == 0
        assert metrics.total_execution_time_ms == 0.0
        assert len(metrics.query_history) == 0

    def test_record_successful_query(self, metrics):
        """Test recording a successful query."""
        metrics.record_query(
            question="Test question",
            sql="SELECT * FROM test",
            execution_time_ms=100.0,
            row_count=10,
            success=True
        )

        assert metrics.total_queries == 1
        assert metrics.successful_queries == 1
        assert metrics.failed_queries == 0
        assert metrics.total_execution_time_ms == 100.0

    def test_record_failed_query(self, metrics):
        """Test recording a failed query."""
        metrics.record_query(
            question="Test question",
            sql="SELECT * FROM test",
            execution_time_ms=50.0,
            row_count=0,
            success=False,
            error="Query failed"
        )

        assert metrics.total_queries == 1
        assert metrics.successful_queries == 0
        assert metrics.failed_queries == 1

    def test_average_execution_time(self, metrics):
        """Test average execution time calculation."""
        metrics.record_query("q1", "sql1", 100.0, 10, True)
        metrics.record_query("q2", "sql2", 200.0, 20, True)
        metrics.record_query("q3", "sql3", 300.0, 30, True)

        assert metrics.average_execution_time_ms == 200.0

    def test_average_execution_time_empty(self, metrics):
        """Test average execution time with no queries."""
        assert metrics.average_execution_time_ms == 0.0

    def test_success_rate(self, metrics):
        """Test success rate calculation."""
        metrics.record_query("q1", "sql1", 100.0, 10, True)
        metrics.record_query("q2", "sql2", 100.0, 10, True)
        metrics.record_query("q3", "sql3", 100.0, 0, False, "error")
        metrics.record_query("q4", "sql4", 100.0, 10, True)

        assert metrics.success_rate == 75.0

    def test_success_rate_empty(self, metrics):
        """Test success rate with no queries."""
        assert metrics.success_rate == 0.0

    def test_query_history_limit(self, metrics):
        """Test that query history is bounded."""
        # Record more than the limit
        for i in range(150):
            metrics.record_query(f"q{i}", f"sql{i}", 10.0, 1, True)

        assert len(metrics.query_history) <= 100

    def test_get_summary(self, metrics):
        """Test get_summary method."""
        metrics.record_query("q1", "sql1", 100.0, 10, True)
        metrics.record_query("q2", "sql2", 200.0, 0, False, "error")

        summary = metrics.get_summary()

        assert summary["total_queries"] == 2
        assert summary["successful_queries"] == 1
        assert summary["failed_queries"] == 1
        assert summary["success_rate_percent"] == 50.0
        assert summary["average_execution_time_ms"] == 150.0
        assert summary["total_execution_time_ms"] == 300.0


class TestJSONFormatter:
    """Test cases for JSONFormatter."""

    def test_format_basic_record(self):
        """Test formatting a basic log record."""
        import json

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Test message",
            args=(),
            exc_info=None
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["message"] == "Test message"
        assert "timestamp" in data


class TestConsoleFormatter:
    """Test cases for ConsoleFormatter."""

    def test_format_basic_record(self):
        """Test formatting a basic log record."""
        formatter = ConsoleFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Test message",
            args=(),
            exc_info=None
        )

        output = formatter.format(record)

        assert "INFO" in output
        assert "Test message" in output
