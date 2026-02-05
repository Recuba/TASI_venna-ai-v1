"""
TASI Financial Database - Vanna AI Agent
Uses Gemini Flash 2.5 via OpenRouter + PostgreSQL
Supports both Streamlit web UI and interactive CLI.
"""

import os
import sys
import io
import re
import json
import csv
import time
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from typing import List, Optional, Any, Dict

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

load_dotenv()

# =============================================================================
# Configuration
# =============================================================================

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tasi:tasi_dev_123@localhost:5433/tasi_financials")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
QUERY_LOG_DIR = Path(__file__).parent / "query_logs"

if not OPENROUTER_API_KEY:
    print("WARNING: OPENROUTER_API_KEY not set. Please set it in your .env file.")
    print("Get your key from: https://openrouter.ai/keys")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


# =============================================================================
# SQL Safety Guardrails
# =============================================================================

# Patterns that indicate destructive or unauthorized SQL
_BLOCKED_SQL_PATTERNS = [
    r"\bDROP\s+(TABLE|DATABASE|SCHEMA|INDEX|VIEW|FUNCTION|EXTENSION|ROLE)\b",
    r"\bDELETE\s+FROM\b",
    r"\bTRUNCATE\b",
    r"\bALTER\s+(TABLE|DATABASE|SCHEMA|ROLE)\b",
    r"\bINSERT\s+INTO\b",
    r"\bUPDATE\s+\w+\s+SET\b",
    r"\bCREATE\s+(TABLE|DATABASE|SCHEMA|INDEX|ROLE|USER)\b",
    r"\bGRANT\b",
    r"\bREVOKE\b",
    r"\bCOPY\b",
    r"\bEXECUTE\b",
    r"\bCALL\b",
    r";\s*\w",  # Multiple statements (SQL injection attempt)
]

_BLOCKED_REGEX = re.compile(
    "|".join(_BLOCKED_SQL_PATTERNS),
    re.IGNORECASE | re.DOTALL,
)


def validate_sql_safety(sql: str) -> tuple[bool, str]:
    """
    Validate that generated SQL is read-only and safe to execute.
    Returns (is_safe, reason).
    """
    cleaned = sql.strip().rstrip(";").strip()

    if not cleaned:
        return False, "Empty SQL query"

    match = _BLOCKED_REGEX.search(cleaned)
    if match:
        return False, f"Blocked: query contains disallowed pattern '{match.group()}'"

    # Must start with a SELECT or WITH (CTE)
    first_word = cleaned.split()[0].upper() if cleaned.split() else ""
    if first_word not in ("SELECT", "WITH", "EXPLAIN"):
        return False, f"Blocked: queries must begin with SELECT or WITH, got '{first_word}'"

    return True, "OK"


# =============================================================================
# Query History & Export
# =============================================================================

class QueryHistory:
    """Tracks query history for the current session and supports export."""

    def __init__(self):
        self.entries: List[Dict[str, Any]] = []

    def log(self, question: str, sql: str, row_count: int, success: bool, error: str = None, duration_ms: float = 0):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "question": question,
            "sql": sql,
            "row_count": row_count,
            "success": success,
            "error": error,
            "duration_ms": round(duration_ms, 1),
        }
        self.entries.append(entry)
        logger.info("Query logged: success=%s rows=%s duration=%.0fms", success, row_count, duration_ms)

    def to_json(self) -> str:
        return json.dumps(self.entries, indent=2, ensure_ascii=False)

    def to_csv(self) -> str:
        if not self.entries:
            return ""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=self.entries[0].keys())
        writer.writeheader()
        writer.writerows(self.entries)
        return output.getvalue()


def export_results_csv(results: List[Dict[str, Any]]) -> str:
    """Export query results as CSV string."""
    if not results:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=results[0].keys())
    writer.writeheader()
    writer.writerows(results)
    return output.getvalue()


def export_results_json(results: List[Dict[str, Any]]) -> str:
    """Export query results as JSON string."""
    def _default(o):
        if hasattr(o, 'isoformat'):
            return o.isoformat()
        if isinstance(o, (set, frozenset)):
            return list(o)
        return str(o)
    return json.dumps(results, indent=2, ensure_ascii=False, default=_default)


# =============================================================================
# OpenRouter LLM Service (Gemini Flash 2.5)
# =============================================================================

from openai import OpenAI

class OpenRouterLlmService:
    """LLM Service using OpenRouter API with Gemini Flash 2.5"""

    def __init__(
        self,
        api_key: str = None,
        model: str = "google/gemini-2.5-flash",
        base_url: str = "https://openrouter.ai/api/v1",
        max_retries: int = 3,
    ):
        self.api_key = api_key or OPENROUTER_API_KEY
        self.model = model
        self.max_retries = max_retries
        self.client = OpenAI(
            base_url=base_url,
            api_key=self.api_key,
        )

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """Send a chat completion request with retry logic."""
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    **kwargs
                )
                return response.choices[0].message.content
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    wait = 2 ** attempt
                    logger.warning("LLM request failed (attempt %d/%d): %s. Retrying in %ds...",
                                   attempt, self.max_retries, e, wait)
                    time.sleep(wait)
        raise RuntimeError(f"LLM request failed after {self.max_retries} attempts: {last_error}")


# =============================================================================
# PostgreSQL Connection (with resilience)
# =============================================================================

import psycopg2
from psycopg2.extras import RealDictCursor

class PostgresRunner:
    """SQL Runner for PostgreSQL with connection resilience."""

    def __init__(self, connection_string: str = None, connect_timeout: int = 10, max_retries: int = 3):
        self.connection_string = connection_string or DATABASE_URL
        self.connect_timeout = connect_timeout
        self.max_retries = max_retries
        self._conn = None

    def get_connection(self):
        """Get or create a database connection with retry logic."""
        if self._conn is not None and not self._conn.closed:
            try:
                # Verify connection is alive
                self._conn.cursor().execute("SELECT 1")
                return self._conn
            except Exception:
                self._close()

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self._conn = psycopg2.connect(
                    self.connection_string,
                    connect_timeout=self.connect_timeout,
                )
                self._conn.set_session(readonly=True, autocommit=True)
                return self._conn
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    wait = 2 ** attempt
                    logger.warning("DB connect failed (attempt %d/%d): %s. Retrying in %ds...",
                                   attempt, self.max_retries, e, wait)
                    time.sleep(wait)
        raise ConnectionError(f"Could not connect to database after {self.max_retries} attempts: {last_error}")

    def _close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def run_sql(self, sql: str) -> List[Dict[str, Any]]:
        """Execute a read-only SQL query and return results as list of dicts."""
        # Safety check
        is_safe, reason = validate_sql_safety(sql)
        if not is_safe:
            raise PermissionError(f"SQL safety check failed: {reason}")

        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(sql)
                if cursor.description:
                    results = cursor.fetchall()
                    return [dict(row) for row in results]
                return []
        except Exception as e:
            # Connection might be broken; reset it
            self._close()
            raise e

    def get_schema(self) -> str:
        """Get database schema information."""
        sql = """
        SELECT
            table_name,
            column_name,
            data_type,
            is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position;
        """
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(sql)
                results = [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self._close()
            raise e

        schema_text = []
        current_table = None

        for row in results:
            if row['table_name'] != current_table:
                current_table = row['table_name']
                schema_text.append(f"\n{current_table}:")

            nullable = "NULL" if row['is_nullable'] == 'YES' else "NOT NULL"
            schema_text.append(f"  - {row['column_name']}: {row['data_type']} ({nullable})")

        return "\n".join(schema_text)

    def __del__(self):
        self._close()


# =============================================================================
# TASI Financial Agent
# =============================================================================

class TASIFinancialAgent:
    """
    Vanna-style agent for TASI financial queries.
    Uses Gemini Flash 2.5 via OpenRouter to convert natural language to SQL.
    """

    def __init__(self):
        self.llm = OpenRouterLlmService()
        self.sql_runner = PostgresRunner()
        self.schema = self.sql_runner.get_schema()
        self.history = QueryHistory()
        self.training_examples = self._load_training_examples()

    def _load_training_examples(self) -> str:
        """Load training examples for few-shot prompting."""
        return """
## Example Queries

Question: "Show all companies"
SQL: SELECT ticker, company_name, sector, company_type, size_category FROM company_financials WHERE is_latest = TRUE GROUP BY ticker, company_name, sector, company_type, size_category ORDER BY company_name;

Question: "Which companies are most profitable?"
SQL: SELECT ticker, company_name, sector, roe_percent, net_profit_millions, revenue_millions FROM company_financials WHERE is_latest = TRUE AND is_annual = TRUE AND profit_status = 'Profit' ORDER BY roe_percent DESC NULLS LAST LIMIT 20;

Question: "Top 10 companies by ROE in 2024"
SQL: SELECT ticker, company_name, sector, roe_percent, net_margin_percent FROM company_financials WHERE fiscal_year = 2024 AND is_annual = TRUE AND profit_status = 'Profit' ORDER BY roe_percent DESC NULLS LAST LIMIT 10;

Question: "Show companies with excellent ROE"
SQL: SELECT ticker, company_name, sector, roe_percent, roe_status FROM company_financials WHERE is_latest = TRUE AND is_annual = TRUE AND roe_status = 'Excellent' ORDER BY roe_percent DESC;

Question: "Largest companies by revenue"
SQL: SELECT ticker, company_name, sector, revenue_millions, net_profit_millions FROM company_financials WHERE is_latest = TRUE AND is_annual = TRUE ORDER BY revenue_millions DESC NULLS LAST LIMIT 20;

Question: "What is the total revenue by sector?"
SQL: SELECT sector, COUNT(DISTINCT ticker) as companies, SUM(revenue_millions) as total_revenue_millions, AVG(roe_percent) as avg_roe_percent FROM company_financials WHERE is_latest = TRUE AND is_annual = TRUE GROUP BY sector ORDER BY total_revenue_millions DESC NULLS LAST;

Question: "Which companies are losing money?"
SQL: SELECT ticker, company_name, sector, net_profit_millions, net_margin_percent FROM company_financials WHERE is_latest = TRUE AND is_annual = TRUE AND profit_status = 'Loss' ORDER BY net_profit_millions ASC;

Question: "Companies with strong liquidity"
SQL: SELECT ticker, company_name, current_ratio, quick_ratio, liquidity_status FROM company_financials WHERE is_latest = TRUE AND is_annual = TRUE AND liquidity_status = 'Strong' ORDER BY current_ratio DESC;

Question: "Most leveraged companies"
SQL: SELECT ticker, company_name, sector, debt_to_equity_percent, leverage_status FROM company_financials WHERE is_latest = TRUE AND is_annual = TRUE ORDER BY debt_to_equity_percent DESC NULLS LAST LIMIT 20;

Question: "Insurance sector performance"
SQL: SELECT ticker, company_name, revenue_millions, net_profit_millions, roe_percent, net_margin_percent, profit_status FROM company_financials WHERE sector = 'Insurance' AND is_latest = TRUE AND is_annual = TRUE ORDER BY revenue_millions DESC NULLS LAST;

Question: "Compare sectors by profitability"
SQL: SELECT sector, COUNT(DISTINCT ticker) as company_count, AVG(roe_percent) as avg_roe, AVG(net_margin_percent) as avg_net_margin, SUM(CASE WHEN profit_status = 'Profit' THEN 1 ELSE 0 END) as profitable_companies FROM company_financials WHERE is_latest = TRUE AND is_annual = TRUE GROUP BY sector ORDER BY avg_roe DESC NULLS LAST;

Question: "Year over year summary"
SQL: SELECT fiscal_year, COUNT(DISTINCT ticker) as companies_reporting, ROUND(SUM(revenue_millions)::numeric, 2) as total_revenue_m, ROUND(SUM(net_profit_millions)::numeric, 2) as total_profit_m, ROUND(AVG(roe_percent)::numeric, 2) as avg_roe_pct FROM company_financials WHERE is_annual = TRUE GROUP BY fiscal_year ORDER BY fiscal_year;
"""

    def _build_system_prompt(self) -> str:
        """Build the system prompt for SQL generation."""
        return f"""You are a SQL expert for the TASI (Saudi Stock Exchange) financial database.
Your task is to convert natural language questions into PostgreSQL SELECT queries.

## Database Schema
The main view for querying is `company_financials` which contains:
{self.schema}

## Key Information
- All monetary values are in MILLIONS of Saudi Riyals (SAR)
- All ratios (roe_percent, net_margin_percent, etc.) are expressed as percentages (e.g., 15 means 15%)
- Use `is_latest = TRUE` to get the most recent data for each company
- Use `is_annual = TRUE` to filter for annual (full-year) data only
- Common status values:
  - profit_status: 'Profit', 'Loss', 'N/A'
  - liquidity_status: 'Strong', 'Moderate', 'Weak', 'Critical'
  - leverage_status: 'Low', 'Moderate', 'High', 'Critical'
  - roe_status: 'Excellent', 'Good', 'Average', 'Weak', 'Negative', 'N/A'

{self.training_examples}

## Instructions
1. Generate ONLY the SQL query, no explanations
2. Always use the `company_financials` view unless specifically asked for raw data
3. Use proper PostgreSQL syntax
4. Handle NULL values with NULLS LAST in ORDER BY
5. Limit results to reasonable numbers (20-50) unless asked for all
6. For "latest" or "current" data, use `is_latest = TRUE`
7. For annual comparisons, use `is_annual = TRUE`
8. ONLY generate SELECT or WITH statements. Never generate INSERT, UPDATE, DELETE, DROP, or any data-modifying queries.
"""

    def generate_sql(self, question: str) -> str:
        """Generate SQL from a natural language question."""
        messages = [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": f"Generate a SQL query for: {question}"}
        ]

        response = self.llm.chat(messages, temperature=0.1, max_tokens=1000)

        # Clean the response - extract just the SQL
        sql = response.strip()

        # Remove markdown code blocks if present
        if sql.startswith("```sql"):
            sql = sql[6:]
        elif sql.startswith("```"):
            sql = sql[3:]
        if sql.endswith("```"):
            sql = sql[:-3]

        return sql.strip()

    def ask(self, question: str) -> Dict[str, Any]:
        """
        Ask a question in natural language and get results.
        Returns the generated SQL, query results, and metadata.
        """
        sql = None
        start = time.time()
        try:
            sql = self.generate_sql(question)
            results = self.sql_runner.run_sql(sql)
            duration_ms = (time.time() - start) * 1000

            self.history.log(question, sql, len(results), True, duration_ms=duration_ms)

            return {
                "question": question,
                "sql": sql,
                "results": results,
                "success": True,
                "error": None,
                "duration_ms": round(duration_ms, 1),
            }
        except Exception as e:
            duration_ms = (time.time() - start) * 1000
            self.history.log(question, sql or "", 0, False, error=str(e), duration_ms=duration_ms)
            return {
                "question": question,
                "sql": sql,
                "results": None,
                "success": False,
                "error": str(e),
                "duration_ms": round(duration_ms, 1),
            }

    def format_results(self, results: List[Dict[str, Any]], max_rows: int = 50) -> str:
        """Format query results as a nice ASCII table."""
        if not results:
            return "No results found."

        columns = list(results[0].keys())

        widths = {col: len(col) for col in columns}
        for row in results[:max_rows]:
            for col in columns:
                val = str(row.get(col, ''))[:50]
                widths[col] = max(widths[col], len(val))

        header = " | ".join(col.ljust(widths[col]) for col in columns)
        separator = "-+-".join("-" * widths[col] for col in columns)

        rows = []
        for row in results[:max_rows]:
            row_str = " | ".join(
                str(row.get(col, ''))[:50].ljust(widths[col])
                for col in columns
            )
            rows.append(row_str)

        table = f"{header}\n{separator}\n" + "\n".join(rows)

        if len(results) > max_rows:
            table += f"\n\n... and {len(results) - max_rows} more rows"

        return table


# =============================================================================
# Streamlit Web UI
# =============================================================================

def run_streamlit():
    """Run the Streamlit web interface."""
    import streamlit as st
    import pandas as pd

    st.set_page_config(
        page_title="TASI Financial AI",
        page_icon="SAR",
        layout="wide",
    )

    # ---- Session state initialization ----
    if "agent" not in st.session_state:
        with st.spinner("Connecting to database and initializing AI agent..."):
            try:
                st.session_state.agent = TASIFinancialAgent()
                st.session_state.db_connected = True
            except Exception as e:
                st.session_state.agent = None
                st.session_state.db_connected = False
                st.session_state.db_error = str(e)

    if "messages" not in st.session_state:
        st.session_state.messages = []

    # ---- Sidebar ----
    with st.sidebar:
        st.title("TASI Financial AI")
        st.caption("Powered by Gemini Flash 2.5 via OpenRouter")

        if not st.session_state.db_connected:
            st.error(f"Database connection failed: {st.session_state.get('db_error', 'Unknown')}")
            st.stop()

        st.success("Database connected")

        st.divider()
        st.subheader("Quick queries")
        quick_queries = [
            "Show the top 10 most profitable companies",
            "Compare sectors by profitability",
            "Which companies are losing money?",
            "Year over year revenue summary",
            "Companies with excellent ROE",
            "Most leveraged companies",
            "Insurance sector performance",
            "Companies with strong liquidity",
        ]
        for q in quick_queries:
            if st.button(q, key=f"quick_{q}", use_container_width=True):
                st.session_state.quick_query = q

        st.divider()

        # Query history export
        agent: TASIFinancialAgent = st.session_state.agent
        if agent.history.entries:
            st.subheader("Session history")
            st.caption(f"{len(agent.history.entries)} queries this session")
            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    "History (JSON)",
                    data=agent.history.to_json(),
                    file_name=f"tasi_query_history_{datetime.now():%Y%m%d_%H%M}.json",
                    mime="application/json",
                    use_container_width=True,
                )
            with col2:
                st.download_button(
                    "History (CSV)",
                    data=agent.history.to_csv(),
                    file_name=f"tasi_query_history_{datetime.now():%Y%m%d_%H%M}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

    # ---- Main area ----
    st.header("Ask questions about TASI-listed companies")

    # Replay chat history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.write(msg["content"])
            else:
                _render_assistant_message(msg)

    # Handle quick-query button clicks
    prompt = None
    if "quick_query" in st.session_state:
        prompt = st.session_state.pop("quick_query")
    else:
        prompt = st.chat_input("Ask a question about TASI financial data...")

    if prompt:
        # Show user message
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        # Generate answer
        with st.chat_message("assistant"):
            with st.spinner("Generating SQL and querying database..."):
                result = agent.ask(prompt)

            msg_data = {"role": "assistant", **result}
            st.session_state.messages.append(msg_data)
            _render_assistant_message(msg_data)


def _render_assistant_message(msg: dict):
    """Render an assistant message in the Streamlit chat."""
    import streamlit as st
    import pandas as pd

    if msg.get("sql"):
        with st.expander("Generated SQL", expanded=False):
            st.code(msg["sql"], language="sql")

    if msg.get("success") and msg.get("results"):
        results = msg["results"]
        df = pd.DataFrame(results)
        st.dataframe(df, use_container_width=True)
        st.caption(f"{len(results)} rows returned in {msg.get('duration_ms', 0):.0f} ms")

        # Export buttons
        col1, col2 = st.columns([1, 1])
        with col1:
            st.download_button(
                "Download CSV",
                data=export_results_csv(results),
                file_name=f"tasi_results_{datetime.now():%Y%m%d_%H%M%S}.csv",
                mime="text/csv",
                key=f"csv_{msg.get('question', '')}_{msg.get('duration_ms', 0)}",
            )
        with col2:
            st.download_button(
                "Download JSON",
                data=export_results_json(results),
                file_name=f"tasi_results_{datetime.now():%Y%m%d_%H%M%S}.json",
                mime="application/json",
                key=f"json_{msg.get('question', '')}_{msg.get('duration_ms', 0)}",
            )
    elif msg.get("success") and not msg.get("results"):
        st.info("Query executed successfully but returned no results.")
    elif not msg.get("success"):
        st.error(f"Error: {msg.get('error', 'Unknown error')}")
        if msg.get("sql"):
            with st.expander("SQL that failed"):
                st.code(msg["sql"], language="sql")


# =============================================================================
# Interactive CLI
# =============================================================================

def run_cli():
    """Interactive CLI for the TASI Financial Agent."""
    print("=" * 60)
    print("TASI Financial Database - Vanna AI Agent")
    print("Powered by Gemini Flash 2.5 via OpenRouter")
    print("=" * 60)
    print("\nInitializing agent...")

    agent = TASIFinancialAgent()

    print("Agent ready! Ask questions about TASI-listed companies.")
    print("Type 'quit' or 'exit' to stop.")
    print("Type 'schema' to view the database schema.")
    print("Type 'history' to view query history.")
    print("Type 'export <format>' to export last results (csv/json).\n")
    print("Example questions:")
    print("  - Show me the top 10 most profitable companies")
    print("  - Which companies have the highest ROE?")
    print("  - Compare sector performance")
    print("  - Show insurance companies with losses")
    print()

    last_results = None

    while True:
        try:
            question = input("\nYour question: ").strip()

            if not question:
                continue

            if question.lower() in ('quit', 'exit', 'q'):
                print("Goodbye!")
                break

            if question.lower() == 'schema':
                print("\nDatabase Schema:")
                print(agent.schema)
                continue

            if question.lower() == 'history':
                if not agent.history.entries:
                    print("No queries in history yet.")
                else:
                    for i, entry in enumerate(agent.history.entries, 1):
                        status = "OK" if entry["success"] else "FAIL"
                        print(f"  {i}. [{status}] {entry['question']} ({entry['row_count']} rows, {entry['duration_ms']}ms)")
                continue

            if question.lower().startswith('export'):
                parts = question.split()
                fmt = parts[1].lower() if len(parts) > 1 else "csv"
                if last_results is None:
                    print("No results to export. Run a query first.")
                    continue
                filename = f"tasi_results_{datetime.now():%Y%m%d_%H%M%S}.{fmt}"
                filepath = Path(filename)
                if fmt == "json":
                    filepath.write_text(export_results_json(last_results), encoding="utf-8")
                else:
                    filepath.write_text(export_results_csv(last_results), encoding="utf-8")
                print(f"Exported {len(last_results)} rows to {filepath}")
                continue

            # Ask the question
            result = agent.ask(question)

            if result["success"]:
                print(f"\nGenerated SQL:\n{result['sql']}\n")
                print("Results:")
                print(agent.format_results(result["results"]))
                print(f"\n({len(result['results'])} rows, {result['duration_ms']:.0f} ms)")
                last_results = result["results"]
            else:
                print(f"\nError: {result['error']}")
                if result["sql"]:
                    print(f"SQL attempted: {result['sql']}")

        except KeyboardInterrupt:
            print("\n\nInterrupted. Goodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")


# =============================================================================
# Entry Point
# =============================================================================

def _is_running_in_streamlit() -> bool:
    """Detect if we are running inside Streamlit."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False


if _is_running_in_streamlit():
    run_streamlit()
elif __name__ == "__main__":
    if "--streamlit" in sys.argv:
        # Allow explicit streamlit launch: python vanna_app.py --streamlit
        os.system(f"streamlit run {__file__} --server.enableCORS false --server.enableXsrfProtection false")
    else:
        run_cli()
