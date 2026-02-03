"""
TASI Financial Database - Streamlit Web Application
Interactive web interface for natural language financial queries.
"""

import streamlit as st
import pandas as pd
import time
from typing import Dict, Any, List, Optional
from datetime import datetime

# Import local modules
try:
    from config import settings, ConfigurationError
    from logger import get_logger, query_metrics
    from sql_validator import SQLValidator, ValidationResult
except ImportError as e:
    st.error(f"Failed to import required modules: {e}")
    st.stop()

# Initialize logger
logger = get_logger("streamlit_app")

# =============================================================================
# Page Configuration
# =============================================================================

st.set_page_config(
    page_title="TASI Financial Database",
    page_icon=":chart_with_upwards_trend:",
    layout="wide",
    initial_sidebar_state="expanded"
)


# =============================================================================
# Database and LLM Services
# =============================================================================

@st.cache_resource
def get_db_connection():
    """Get cached database connection."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    try:
        conn = psycopg2.connect(settings.database.url)
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise


@st.cache_resource
def get_llm_client():
    """Get cached LLM client."""
    from openai import OpenAI

    return OpenAI(
        base_url=settings.llm.base_url,
        api_key=settings.llm.api_key,
    )


def get_schema() -> str:
    """Get database schema information."""
    conn = get_db_connection()
    sql = """
    SELECT table_name, column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_schema = 'public'
    ORDER BY table_name, ordinal_position;
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            results = cursor.fetchall()

        schema_text = []
        current_table = None

        for row in results:
            if row[0] != current_table:
                current_table = row[0]
                schema_text.append(f"\n{current_table}:")

            nullable = "NULL" if row[3] == 'YES' else "NOT NULL"
            schema_text.append(f"  - {row[1]}: {row[2]} ({nullable})")

        return "\n".join(schema_text)
    except Exception as e:
        logger.error(f"Failed to get schema: {e}")
        return "Schema unavailable"


def get_training_examples() -> str:
    """Get training examples for few-shot prompting."""
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

Question: "Compare sectors by profitability"
SQL: SELECT sector, COUNT(DISTINCT ticker) as company_count, AVG(roe_percent) as avg_roe, AVG(net_margin_percent) as avg_net_margin, SUM(CASE WHEN profit_status = 'Profit' THEN 1 ELSE 0 END) as profitable_companies FROM company_financials WHERE is_latest = TRUE AND is_annual = TRUE GROUP BY sector ORDER BY avg_roe DESC NULLS LAST;

Question: "Year over year summary"
SQL: SELECT fiscal_year, COUNT(DISTINCT ticker) as companies_reporting, ROUND(SUM(revenue_millions)::numeric, 2) as total_revenue_m, ROUND(SUM(net_profit_millions)::numeric, 2) as total_profit_m, ROUND(AVG(roe_percent)::numeric, 2) as avg_roe_pct FROM company_financials WHERE is_annual = TRUE GROUP BY fiscal_year ORDER BY fiscal_year;
"""


def build_system_prompt(schema: str) -> str:
    """Build the system prompt for SQL generation."""
    return f"""You are a SQL expert for the TASI (Saudi Stock Exchange) financial database.
Your task is to convert natural language questions into PostgreSQL queries.

## Database Schema
The main view for querying is `company_financials` which contains:
{schema}

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

{get_training_examples()}

## Instructions
1. Generate ONLY the SQL query, no explanations
2. Always use the `company_financials` view unless specifically asked for raw data
3. Use proper PostgreSQL syntax
4. Handle NULL values with NULLS LAST in ORDER BY
5. Limit results to reasonable numbers (20-50) unless asked for all
6. For "latest" or "current" data, use `is_latest = TRUE`
7. For annual comparisons, use `is_annual = TRUE`
"""


def generate_sql(question: str, schema: str) -> str:
    """Generate SQL from a natural language question."""
    client = get_llm_client()

    messages = [
        {"role": "system", "content": build_system_prompt(schema)},
        {"role": "user", "content": f"Generate a SQL query for: {question}"}
    ]

    response = client.chat.completions.create(
        model=settings.llm.model,
        messages=messages,
        temperature=settings.llm.temperature,
        max_tokens=settings.llm.max_tokens
    )

    sql = response.choices[0].message.content.strip()

    # Clean markdown code blocks
    if sql.startswith("```sql"):
        sql = sql[6:]
    elif sql.startswith("```"):
        sql = sql[3:]
    if sql.endswith("```"):
        sql = sql[:-3]

    return sql.strip()


def execute_sql(sql: str) -> pd.DataFrame:
    """Execute SQL and return results as DataFrame."""
    conn = get_db_connection()

    try:
        df = pd.read_sql_query(sql, conn)
        return df
    except Exception as e:
        logger.error(f"SQL execution error: {e}", sql=sql[:200])
        raise


# =============================================================================
# Streamlit UI Components
# =============================================================================

def render_sidebar():
    """Render the sidebar with settings and info."""
    with st.sidebar:
        st.title(":chart_with_upwards_trend: TASI Financial DB")
        st.markdown("---")

        # App information
        st.subheader("About")
        st.markdown("""
        Query Saudi stock exchange financial data using natural language.

        **Features:**
        - Natural language to SQL conversion
        - Interactive data exploration
        - Export results to CSV
        """)

        st.markdown("---")

        # Query examples
        st.subheader("Example Questions")
        examples = [
            "Show me the top 10 most profitable companies",
            "Which sectors have the highest average ROE?",
            "List companies losing money in 2024",
            "Compare banking vs insurance sector performance",
            "Show revenue trends over the years",
        ]
        for example in examples:
            if st.button(example, key=f"ex_{hash(example)}"):
                st.session_state.question = example

        st.markdown("---")

        # Metrics
        if query_metrics.total_queries > 0:
            st.subheader("Session Metrics")
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Total Queries", query_metrics.total_queries)
                st.metric("Success Rate", f"{query_metrics.success_rate:.1f}%")
            with col2:
                st.metric("Successful", query_metrics.successful_queries)
                st.metric("Avg Time", f"{query_metrics.average_execution_time_ms:.0f}ms")

        st.markdown("---")

        # Settings
        with st.expander("Settings"):
            st.checkbox("Show SQL Query", value=True, key="show_sql")
            st.checkbox("Show Query Validation", value=True, key="show_validation")
            st.number_input("Max Rows", min_value=10, max_value=1000, value=100, key="max_rows")


def render_main_content():
    """Render the main content area."""
    st.title("TASI Financial Database")
    st.markdown("Ask questions about Saudi stock exchange companies in plain English.")

    # Initialize session state
    if "question" not in st.session_state:
        st.session_state.question = ""
    if "history" not in st.session_state:
        st.session_state.history = []

    # Query input
    question = st.text_input(
        "Your question:",
        value=st.session_state.question,
        placeholder="e.g., Show me the top 10 companies by ROE",
        key="query_input"
    )

    col1, col2, col3 = st.columns([1, 1, 4])
    with col1:
        run_query = st.button("Ask", type="primary", use_container_width=True)
    with col2:
        clear_history = st.button("Clear History", use_container_width=True)

    if clear_history:
        st.session_state.history = []
        st.rerun()

    # Process query
    if run_query and question:
        process_query(question)

    # Display history
    if st.session_state.history:
        st.markdown("---")
        st.subheader("Query History")

        for i, item in enumerate(reversed(st.session_state.history)):
            with st.expander(f"Q: {item['question'][:80]}...", expanded=(i == 0)):
                render_query_result(item)


def process_query(question: str):
    """Process a natural language query."""
    start_time = time.time()
    validator = SQLValidator(max_rows=st.session_state.get("max_rows", 100))

    result = {
        "question": question,
        "timestamp": datetime.now().isoformat(),
        "sql": None,
        "validation": None,
        "data": None,
        "error": None,
        "execution_time_ms": 0,
    }

    with st.spinner("Generating SQL query..."):
        try:
            # Get schema
            schema = get_schema()

            # Generate SQL
            sql = generate_sql(question, schema)
            result["sql"] = sql

            # Validate SQL
            validation = validator.validate(sql)
            result["validation"] = {
                "is_valid": validation.is_valid,
                "errors": validation.errors,
                "warnings": validation.warnings,
                "modified": validation.modified
            }

            if not validation.is_valid:
                result["error"] = "Query validation failed: " + "; ".join(validation.errors)
                logger.warning("Query validation failed", errors=validation.errors)
            else:
                # Execute validated SQL
                with st.spinner("Executing query..."):
                    df = execute_sql(validation.sql)
                    result["data"] = df
                    result["row_count"] = len(df)

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Query processing failed: {e}")

    # Calculate execution time
    result["execution_time_ms"] = (time.time() - start_time) * 1000

    # Record metrics
    query_metrics.record_query(
        question=question,
        sql=result["sql"] or "",
        execution_time_ms=result["execution_time_ms"],
        row_count=result.get("row_count", 0),
        success=result["error"] is None,
        error=result["error"]
    )

    # Add to history
    st.session_state.history.append(result)

    # Clear question input
    st.session_state.question = ""


def render_query_result(result: Dict[str, Any]):
    """Render a query result."""
    # Show timestamp and execution time
    st.caption(f"Executed at {result['timestamp']} | {result['execution_time_ms']:.0f}ms")

    # Show SQL if enabled
    if st.session_state.get("show_sql", True) and result.get("sql"):
        st.markdown("**Generated SQL:**")
        st.code(result["sql"], language="sql")

    # Show validation results if enabled
    if st.session_state.get("show_validation", True) and result.get("validation"):
        validation = result["validation"]
        if validation["errors"]:
            for error in validation["errors"]:
                st.error(f":x: {error}")
        if validation["warnings"]:
            for warning in validation["warnings"]:
                st.warning(f":warning: {warning}")
        if validation["modified"]:
            st.info(":information_source: Query was modified for safety (e.g., LIMIT added)")

    # Show error if any
    if result.get("error"):
        st.error(f":x: Error: {result['error']}")

    # Show results
    if result.get("data") is not None:
        df = result["data"]
        st.success(f":white_check_mark: Found {len(df)} results")

        # Display data
        st.dataframe(df, use_container_width=True)

        # Export option
        csv = df.to_csv(index=False)
        st.download_button(
            label="Download CSV",
            data=csv,
            file_name=f"tasi_query_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )

        # Show basic statistics for numeric columns
        numeric_cols = df.select_dtypes(include=['number']).columns
        if len(numeric_cols) > 0 and len(df) > 5:
            with st.expander("Show Statistics"):
                st.dataframe(df[numeric_cols].describe())


def render_error_page(error: str):
    """Render error page when configuration fails."""
    st.error(f":x: Configuration Error")
    st.markdown(f"""
    ### Unable to start the application

    **Error:** {error}

    ### Setup Instructions

    1. Copy `.env.example` to `.env`
    2. Fill in the required values:
       - `DATABASE_URL`: PostgreSQL connection string
       - `OPENROUTER_API_KEY`: Your OpenRouter API key

    3. Start the database:
       ```bash
       docker-compose up -d
       ```

    4. Run the setup script:
       ```bash
       python setup_database.py
       ```

    5. Restart this application:
       ```bash
       streamlit run streamlit_app.py
       ```
    """)


# =============================================================================
# Main Application
# =============================================================================

def main():
    """Main application entry point."""
    try:
        # Validate configuration
        settings.validate_all()

        # Test database connection
        get_db_connection()

        # Render UI
        render_sidebar()
        render_main_content()

    except ConfigurationError as e:
        render_error_page(str(e))

    except Exception as e:
        logger.error(f"Application error: {e}")
        st.error(f":x: Application Error: {e}")
        st.markdown("Check the logs for more details.")


if __name__ == "__main__":
    main()
