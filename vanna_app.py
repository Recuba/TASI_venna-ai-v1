"""
TASI Financial Database - Vanna 2.0 Agent
Uses Gemini Flash 2.5 via OpenRouter + PostgreSQL + ChromaDB
"""

import os
import sys
import io
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from typing import Type, List, Optional, Any, Dict
from pydantic import BaseModel, Field

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

load_dotenv()

# =============================================================================
# Configuration
# =============================================================================

# SECURITY: Use environment variables only - no hardcoded credentials!
# Copy .env.example to .env and configure your settings there.
DATABASE_URL = os.getenv("DATABASE_URL")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
CHROMA_PERSIST_DIR = Path(__file__).parent / "chroma_db"

# Validate required configuration
if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable is required.")
    print("Copy .env.example to .env and configure your database connection.")
    sys.exit(1)

if not OPENROUTER_API_KEY:
    print("ERROR: OPENROUTER_API_KEY environment variable is required.")
    print("Get your API key from https://openrouter.ai/keys")
    sys.exit(1)


# =============================================================================
# OpenRouter LLM Service (Gemini Flash 2.5)
# =============================================================================

from openai import OpenAI

# Import SQL validator for security
try:
    from sql_validator import SQLValidator
    SQL_VALIDATOR_AVAILABLE = True
except ImportError:
    SQL_VALIDATOR_AVAILABLE = False
    print("Warning: SQL validator not available. Running without query validation.")

class OpenRouterLlmService:
    """LLM Service using OpenRouter API with Gemini Flash 2.5"""

    def __init__(
        self,
        api_key: str = None,
        model: str = "google/gemini-2.5-flash",  # Correct OpenRouter model ID
        base_url: str = "https://openrouter.ai/api/v1"
    ):
        self.api_key = api_key or OPENROUTER_API_KEY
        self.model = model
        self.client = OpenAI(
            base_url=base_url,
            api_key=self.api_key,
        )

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """Send a chat completion request."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **kwargs
        )
        return response.choices[0].message.content

    async def achat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """Async chat completion."""
        # For simplicity, using sync in thread
        return self.chat(messages, **kwargs)


# =============================================================================
# PostgreSQL Connection
# =============================================================================

import psycopg2
from psycopg2.extras import RealDictCursor

class PostgresRunner:
    """SQL Runner for PostgreSQL"""

    def __init__(self, connection_string: str = None):
        self.connection_string = connection_string or DATABASE_URL
        self._conn = None

    def get_connection(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.connection_string)
        return self._conn

    def run_sql(self, sql: str) -> List[Dict[str, Any]]:
        """Execute SQL and return results as list of dicts."""
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(sql)
                if cursor.description:
                    results = cursor.fetchall()
                    return [dict(row) for row in results]
                conn.commit()
                return [{"status": "success", "rowcount": cursor.rowcount}]
        except Exception as e:
            conn.rollback()
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
        results = self.run_sql(sql)

        schema_text = []
        current_table = None

        for row in results:
            if row['table_name'] != current_table:
                current_table = row['table_name']
                schema_text.append(f"\n{current_table}:")

            nullable = "NULL" if row['is_nullable'] == 'YES' else "NOT NULL"
            schema_text.append(f"  - {row['column_name']}: {row['data_type']} ({nullable})")

        return "\n".join(schema_text)


# =============================================================================
# Simple Vanna-style Agent
# =============================================================================

class TASIFinancialAgent:
    """
    Simple Vanna-style agent for TASI financial queries.
    Uses Gemini Flash 2.5 via OpenRouter to convert natural language to SQL.
    """

    def __init__(self):
        self.llm = OpenRouterLlmService()
        self.sql_runner = PostgresRunner()
        self.schema = self.sql_runner.get_schema()

        # Training examples for better SQL generation
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
Your task is to convert natural language questions into PostgreSQL queries.

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
        Returns both the generated SQL and the query results.
        """
        sql = None
        try:
            # Generate SQL
            sql = self.generate_sql(question)
            print(f"\nGenerated SQL:\n{sql}\n")

            # Validate SQL for security (if validator is available)
            if SQL_VALIDATOR_AVAILABLE:
                validator = SQLValidator()
                validation = validator.validate(sql)

                if not validation.is_valid:
                    error_msg = "; ".join(validation.errors)
                    print(f"SQL Validation Failed: {error_msg}")
                    return {
                        "question": question,
                        "sql": sql,
                        "results": None,
                        "success": False,
                        "error": f"SQL validation failed: {error_msg}"
                    }

                # Use the validated (potentially modified) SQL
                sql = validation.sql

                if validation.warnings:
                    for warning in validation.warnings:
                        print(f"Warning: {warning}")

            # Execute SQL
            results = self.sql_runner.run_sql(sql)

            return {
                "question": question,
                "sql": sql,
                "results": results,
                "success": True,
                "error": None
            }
        except Exception as e:
            return {
                "question": question,
                "sql": sql,
                "results": None,
                "success": False,
                "error": str(e)
            }

    def format_results(self, results: List[Dict[str, Any]], max_rows: int = 20) -> str:
        """Format query results as a nice table."""
        if not results:
            return "No results found."

        # Get column names
        columns = list(results[0].keys())

        # Calculate column widths
        widths = {col: len(col) for col in columns}
        for row in results[:max_rows]:
            for col in columns:
                val = str(row.get(col, ''))[:50]  # Truncate long values
                widths[col] = max(widths[col], len(val))

        # Build header
        header = " | ".join(col.ljust(widths[col]) for col in columns)
        separator = "-+-".join("-" * widths[col] for col in columns)

        # Build rows
        rows = []
        for row in results[:max_rows]:
            row_str = " | ".join(
                str(row.get(col, ''))[:50].ljust(widths[col])
                for col in columns
            )
            rows.append(row_str)

        # Combine
        table = f"{header}\n{separator}\n" + "\n".join(rows)

        if len(results) > max_rows:
            table += f"\n\n... and {len(results) - max_rows} more rows"

        return table


# =============================================================================
# Interactive CLI
# =============================================================================

def main():
    """Interactive CLI for the TASI Financial Agent."""
    print("=" * 60)
    print("TASI Financial Database - Vanna AI Agent")
    print("Powered by Gemini Flash 2.5 via OpenRouter")
    print("=" * 60)
    print("\nInitializing agent...")

    agent = TASIFinancialAgent()

    print("Agent ready! Ask questions about TASI-listed companies.")
    print("Type 'quit' or 'exit' to stop.\n")
    print("Example questions:")
    print("  - Show me the top 10 most profitable companies")
    print("  - Which companies have the highest ROE?")
    print("  - Compare sector performance")
    print("  - Show insurance companies with losses")
    print()

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

            # Ask the question
            result = agent.ask(question)

            if result["success"]:
                print("\nResults:")
                print(agent.format_results(result["results"]))
            else:
                print(f"\nError: {result['error']}")
                if result["sql"]:
                    print(f"SQL attempted: {result['sql']}")

        except KeyboardInterrupt:
            print("\n\nInterrupted. Goodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")


if __name__ == "__main__":
    main()
