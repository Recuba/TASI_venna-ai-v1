"""
TASI Financial Database - Structured Logging Module
Provides consistent logging across all application components.
"""

import logging
import sys
import json
from datetime import datetime
from typing import Optional, Any, Dict
from pathlib import Path
from functools import wraps
import time
import traceback


class JSONFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.
    Useful for log aggregation systems like ELK, Datadog, etc.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": traceback.format_exception(*record.exc_info) if record.exc_info[0] else None
            }

        # Add extra fields
        if hasattr(record, "extra_data"):
            log_data["extra"] = record.extra_data

        return json.dumps(log_data)


class ConsoleFormatter(logging.Formatter):
    """
    Human-readable formatter for console output.
    Includes colors for different log levels.
    """

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        # Check if we're in a terminal that supports colors
        use_colors = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

        level_color = self.COLORS.get(record.levelname, "")
        reset = self.RESET if use_colors else ""
        level_color = level_color if use_colors else ""

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = record.getMessage()

        formatted = f"{timestamp} | {level_color}{record.levelname:8}{reset} | {record.name} | {message}"

        if record.exc_info:
            formatted += "\n" + "".join(traceback.format_exception(*record.exc_info))

        return formatted


class TASILogger:
    """
    Custom logger wrapper with additional functionality.
    Supports structured logging, query tracking, and performance metrics.
    """

    def __init__(self, name: str, level: str = "INFO", log_file: Optional[str] = None):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, level.upper()))
        self.logger.handlers = []  # Clear existing handlers

        # Console handler with human-readable format
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(ConsoleFormatter())
        self.logger.addHandler(console_handler)

        # File handler with JSON format (if specified)
        if log_file:
            file_path = Path(log_file)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file)
            file_handler.setFormatter(JSONFormatter())
            self.logger.addHandler(file_handler)

    def debug(self, message: str, **extra):
        self._log("debug", message, **extra)

    def info(self, message: str, **extra):
        self._log("info", message, **extra)

    def warning(self, message: str, **extra):
        self._log("warning", message, **extra)

    def error(self, message: str, **extra):
        self._log("error", message, **extra)

    def critical(self, message: str, **extra):
        self._log("critical", message, **extra)

    def _log(self, level: str, message: str, **extra):
        """Internal logging method that handles extra data."""
        record = self.logger.makeRecord(
            self.logger.name,
            getattr(logging, level.upper()),
            "(unknown file)", 0, message, (), None
        )
        if extra:
            record.extra_data = extra
        self.logger.handle(record)

    def log_query(self, question: str, sql: str, execution_time_ms: float,
                  row_count: int, success: bool, error: Optional[str] = None):
        """Log a query execution with structured data."""
        log_data = {
            "event": "query_execution",
            "question": question[:200],  # Truncate long questions
            "sql": sql[:500],  # Truncate long SQL
            "execution_time_ms": round(execution_time_ms, 2),
            "row_count": row_count,
            "success": success,
        }
        if error:
            log_data["error"] = error

        if success:
            self.info(f"Query executed successfully in {execution_time_ms:.2f}ms", **log_data)
        else:
            self.error(f"Query failed: {error}", **log_data)


# Global logger instances
_loggers: Dict[str, TASILogger] = {}


def get_logger(name: str = "tasi", level: str = "INFO",
               log_file: Optional[str] = None) -> TASILogger:
    """
    Get or create a logger instance.

    Args:
        name: Logger name (used for namespacing)
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for JSON log output

    Returns:
        TASILogger instance
    """
    if name not in _loggers:
        _loggers[name] = TASILogger(name, level, log_file)
    return _loggers[name]


def log_execution_time(logger_name: str = "tasi"):
    """
    Decorator to log function execution time.

    Usage:
        @log_execution_time("my_module")
        def my_function():
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_logger(logger_name)
            start_time = time.time()

            try:
                result = func(*args, **kwargs)
                execution_time = (time.time() - start_time) * 1000

                logger.debug(
                    f"{func.__name__} completed",
                    function=func.__name__,
                    execution_time_ms=round(execution_time, 2)
                )
                return result

            except Exception as e:
                execution_time = (time.time() - start_time) * 1000
                logger.error(
                    f"{func.__name__} failed: {str(e)}",
                    function=func.__name__,
                    execution_time_ms=round(execution_time, 2),
                    error=str(e)
                )
                raise

        return wrapper
    return decorator


class QueryMetrics:
    """
    Track query performance metrics for monitoring.
    """

    def __init__(self):
        self.total_queries = 0
        self.successful_queries = 0
        self.failed_queries = 0
        self.total_execution_time_ms = 0.0
        self.query_history: list = []
        self._max_history = 100

    def record_query(self, question: str, sql: str, execution_time_ms: float,
                     row_count: int, success: bool, error: Optional[str] = None):
        """Record a query execution."""
        self.total_queries += 1
        self.total_execution_time_ms += execution_time_ms

        if success:
            self.successful_queries += 1
        else:
            self.failed_queries += 1

        # Store in history (limited size)
        self.query_history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "question": question[:200],
            "sql": sql[:500],
            "execution_time_ms": execution_time_ms,
            "row_count": row_count,
            "success": success,
            "error": error
        })

        # Keep history bounded
        if len(self.query_history) > self._max_history:
            self.query_history = self.query_history[-self._max_history:]

    @property
    def average_execution_time_ms(self) -> float:
        """Get average query execution time."""
        if self.total_queries == 0:
            return 0.0
        return self.total_execution_time_ms / self.total_queries

    @property
    def success_rate(self) -> float:
        """Get query success rate as percentage."""
        if self.total_queries == 0:
            return 0.0
        return (self.successful_queries / self.total_queries) * 100

    def get_summary(self) -> Dict[str, Any]:
        """Get metrics summary."""
        return {
            "total_queries": self.total_queries,
            "successful_queries": self.successful_queries,
            "failed_queries": self.failed_queries,
            "success_rate_percent": round(self.success_rate, 2),
            "average_execution_time_ms": round(self.average_execution_time_ms, 2),
            "total_execution_time_ms": round(self.total_execution_time_ms, 2)
        }


# Global metrics instance
query_metrics = QueryMetrics()
