# TASI Financial Database with Vanna AI

A financial analysis platform for Saudi Tadawul Stock Exchange (TASI) listed companies, powered by natural language query capabilities using Gemini Flash 2.5 via OpenRouter.

Ask questions in plain English like _"Which companies have the highest ROE?"_ and get instant SQL-powered answers from a normalized financial database covering quarterly and annual statements.

## Features

- **Natural Language Queries** -- Ask financial questions in plain English; the AI generates and executes SQL automatically
- **Streamlit Web UI** -- Interactive chat-based interface with data tables, SQL preview, and one-click CSV/JSON export
- **CLI Mode** -- Terminal-based interactive agent for quick analysis
- **SQL Safety Guardrails** -- All LLM-generated SQL is validated as read-only before execution
- **Connection Resilience** -- Automatic retry with exponential backoff for both database and LLM connections
- **Query History** -- Full session logging with export to CSV/JSON
- **Normalized Schema** -- Star-schema design with 5 tables, materialized view, and pgvector semantic search support
- **4,800+ Records** -- Quarterly and annual financial data for TASI-listed companies

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 15+ with pgvector extension (or use the included Docker setup)
- An [OpenRouter API key](https://openrouter.ai/keys)

### 1. Clone and configure

```bash
git clone <repo-url>
cd TASI_venna-ai-v1
cp .env.example .env
# Edit .env with your DATABASE_URL and OPENROUTER_API_KEY
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Start the database

**Option A: Docker (recommended for local dev)**

```bash
docker-compose up -d
# PostgreSQL available at localhost:5433
# Default credentials: tasi / tasi_dev_123
```

**Option B: Use your own PostgreSQL or Neon serverless**

Set `DATABASE_URL` in `.env` to point to your instance.

### 4. Load the schema and data

```bash
python setup_database.py
```

This will create tables, load the CSV data, calculate metrics, and refresh the materialized view.

### 5. Run the application

**Web UI (Streamlit):**

```bash
streamlit run vanna_app.py --server.enableCORS false --server.enableXsrfProtection false
# Opens at http://localhost:8501
```

**CLI:**

```bash
python vanna_app.py
```

## Project Structure

```
.
├── vanna_app.py            # Main application (Streamlit UI + CLI)
├── setup_database.py       # Complete database setup script
├── migrate_data.py         # Standalone data migration
├── TASI_financials_DB.csv  # Source financial data (~4,800 rows)
├── docker-compose.yml      # PostgreSQL 16 + pgvector
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable template
├── schema/
│   ├── 01_schema.sql       # Database DDL (tables, views, indexes, functions)
│   ├── 02_etl_migrate.py   # Class-based ETL migration
│   ├── 03_vanna_training.py# Vanna AI training data
│   ├── 04_sample_queries.sql # 22 example SQL queries
│   └── README.md           # Schema documentation
├── tests/
│   └── test_core.py        # Unit tests for SQL safety, export, history
└── .devcontainer/
    └── devcontainer.json   # VS Code / Codespaces dev container
```

## Database Schema

The database uses a star-schema pattern optimized for financial analysis:

| Table | Purpose |
|-------|---------|
| `sectors` | GICS sector classifications |
| `companies` | Master company list with ticker, type, size |
| `fiscal_periods` | Standardized quarterly/annual periods |
| `financial_statements` | Core financials (income, balance sheet, cash flow) in SAR |
| `financial_metrics` | Calculated ratios (ROE, ROA, margins, liquidity, leverage) |
| `company_financials` | **Materialized view** -- denormalized, values in millions SAR and percentages |

All user-facing queries use the `company_financials` view which provides:
- Monetary values in **millions of SAR**
- Ratios as **percentages** (e.g., 15 = 15%)
- Status classifications: `profit_status`, `liquidity_status`, `leverage_status`, `roe_status`
- Convenience filters: `is_latest`, `is_annual`

## Example Questions

- _Show the top 10 most profitable companies_
- _Compare sectors by profitability_
- _Which companies are losing money?_
- _Year over year revenue summary_
- _Companies with excellent ROE_
- _Most leveraged companies_
- _Insurance sector performance_
- _Revenue trend for company 4191_

## Configuration

All configuration is via environment variables (see `.env.example`):

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key for Gemini Flash 2.5 |
| `VANNA_API_KEY` | No | Vanna AI cloud training key |
| `VANNA_MODEL` | No | Vanna model name (default: `tasi-financials`) |
| `OPENAI_API_KEY` | No | OpenAI key for vector embeddings |

## Running Tests

```bash
python -m pytest tests/ -v
```

## Dev Container / Codespaces

The project includes a devcontainer configuration for VS Code and GitHub Codespaces. Opening the project in a devcontainer will automatically:

1. Set up Python 3.11
2. Install all dependencies
3. Launch the Streamlit web UI on port 8501

## License

Private -- all rights reserved.
