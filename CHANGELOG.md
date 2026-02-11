# TASI Venna AI v1 - Repository Changelog

## Commit History (Latest First)

---

### Commit 2: Added Dev Container Folder

- **Hash**: `e7a38a4`
- **Author**: Recuba
- **Date**: 2026-02-03 22:31:09 +0300 (Tuesday, Feb 3, 2026 at 10:31 PM AST)
- **Branch**: master

**Summary**: Added VS Code Dev Container configuration for containerized development.

**Files Changed (1 file, +33 lines)**:

| File | Change |
|------|--------|
| `.devcontainer/devcontainer.json` | Added - VS Code dev container config (Python 3.11, Streamlit auto-launch on port 8501, extensions: Python, Pylance) |

---

### Commit 1: Initial commit: TASI Financial Database with Vanna AI

- **Hash**: `b49ec23`
- **Author**: Recuba (Co-Authored-By: Claude Opus 4.5)
- **Date**: 2026-02-03 08:45:46 +0300 (Tuesday, Feb 3, 2026 at 8:45 AM AST)
- **Branch**: master

**Summary**: Full project initialization - a financial data analytics platform built around the Saudi TASI (Tadawul All Share Index) stock market, combining PostgreSQL with Vanna AI for natural language SQL queries.

**Files Changed (13 files, +7,799 lines)**:

| File | Change |
|------|--------|
| `.env.example` | Added - Environment variable template (DB connection, API keys) |
| `.gitignore` | Added - Git ignore rules |
| `TASI_financials_DB.csv` | Added - Source financial dataset (4,827 lines, 302 TASI-listed companies) |
| `docker-compose.yml` | Added - PostgreSQL 16 + pgvector container (port 5433) |
| `migrate_data.py` | Added - CSV-to-PostgreSQL ETL migration script |
| `requirements.txt` | Added - Python dependencies (psycopg2, pgvector, pandas, vanna, openai, streamlit, etc.) |
| `schema/01_schema.sql` | Added - Normalized PostgreSQL schema (sectors, companies, fiscal_periods, financial_statements, financial_metrics, company_financials materialized view) |
| `schema/02_etl_migrate.py` | Added - Schema-level ETL migration script |
| `schema/03_vanna_training.py` | Added - Vanna AI training script (teach AI the schema, columns, sample queries) |
| `schema/04_sample_queries.sql` | Added - Example SQL queries (profitability, sector analysis, leverage, time-series) |
| `schema/README.md` | Added - Schema documentation |
| `setup_database.py` | Added - Database initialization and schema setup script |
| `vanna_app.py` | Added - Main Streamlit application (NL-to-SQL interface via Gemini Flash 2.5) |

**Key Features Introduced**:
- PostgreSQL + pgvector database schema for TASI financial data
- Normalized star schema (dimension tables: companies, sectors, periods; fact table: financial_statements)
- Materialized view (`company_financials`) for easy querying
- Data migration pipeline for CSV import (4,748 financial records)
- Vanna AI agent using Gemini Flash 2.5 via OpenRouter for natural language to SQL conversion
- Streamlit web UI on port 8501
- Docker Compose setup for local development

---

## Repository Overview

| Property | Value |
|----------|-------|
| **Project** | TASI Financial Database with Vanna AI |
| **Language** | Python 3.11 |
| **Database** | PostgreSQL 16 + pgvector |
| **AI/LLM** | Vanna 2.0 + Gemini Flash 2.5 (via OpenRouter) |
| **Frontend** | Streamlit |
| **Total Commits** | 2 |
| **Total Files** | 14 |
| **Primary Author** | Recuba |
| **First Commit** | 2026-02-03 08:45:46 +0300 |
| **Latest Commit** | 2026-02-03 22:31:09 +0300 |

*Document generated on 2026-02-11*
