# HVIA Task 04 — Amira Al Sebou

**Advanced Data Engineering, BI & AI Challenge**  
*Theme: From Messy Data to Trusted Decisions*

---

## Overview

This repository houses the end-to-end data engineering, data quality, machine learning, local AI, and Business Intelligence solution for **HVIA Task 04 (Amira Al Sebou)**. The project transforms a multi-table PostgreSQL business database (42 connected tables across sales, products, customers, inventory, suppliers, and treasury) into a validated analytical foundation powering five interactive Streamlit applications.

---

## Mandatory Privacy, Security & Data Integrity Principles

1. **Source Data Immutability:** The original PostgreSQL dump and `raw` schema remain strictly read-only and unmodified.
2. **Historical Fact Preservation:** Historical invoice unit prices and transaction totals are never overwritten with current catalog prices.
3. **100% Local AI Only:** All LLM-assisted diagnostics run strictly locally via **Ollama** using `qwen3.5:4b` (or local `qwen3.5:9b`). Company data must **never** be sent to external APIs, cloud LLMs, or remote Ollama models (such as `kimi-k3:cloud`).
4. **AI-Assisted & Deterministic Verification:** Deterministic Tier-1 normalization (Unicode NFC Arabic cleaning, digits, dirty phone flagging) runs unconditionally; local Ollama `qwen3.5:4b` handles Tier-2 ambiguity (product name clusters, manual-to-catalog matching). "No manual preprocessing" means no manual Excel/human record edits — never "no deterministic code." Every AI suggestion is verified by Python/SQL rules before staging writes.
5. **Auditability & Zero Silent Deletion:** Every transformation is recorded in an auditable repair log (`old_value`, `new_value`, `rule`, `reason`, `timestamp`); records are never arbitrarily deleted.
6. **Zero Data Exposure:** Database dumps (`.sql`), `.env` files, logs, and generated business reports are excluded from version control via `.gitignore`.

---

## Architecture Summary

### Data Pipeline Flow

1. **Restore (`raw`):** Load the original PostgreSQL dump into an isolated database instance (`amira_training_lab`) with an untouched `raw` schema copy (full DDL preserved via `LIKE ... INCLUDING ALL`).
2. **Data Quality (`Profile -> Diagnose -> Repair -> Validate -> Document`):**
   - **Tier 1 (Deterministic Rules):** Schema, completeness, Unicode NFC Arabic normalization, financial arithmetic checks, outlier detection, and referential integrity.
   - **Tier 2 (Local AI Advisor):** Summarized metadata/pattern review via local Ollama `qwen3.5:4b` (never raw PII).
   - **Tier 3 (Human Approval):** Review queue for ambiguous duplicate clusters or business rule conflicts.
3. **ETL (`raw -> staging -> analytics`):** Idempotent Python + SQLAlchemy pipelines writing cleaned/annotated records into `staging` and analytical marts/views into `analytics`.
4. **BI & ML Applications (Streamlit + Plotly):**
   - **App 01 — Executive Command Center:** Sales, orders, collections, refunds, branches, and sales representatives.
   - **App 02 — Product Pricing & Profit Lab:** Catalog vs. historical invoice prices, cost breakdown, margin bands, and what-if price simulations.
   - **App 03 — Customer 360 & Retention:** RFM scoring, segmentation (KMeans validation), repeat purchase behavior, and cohort retention.
   - **App 04 — Treasury Reconciliation:** Cash position by branch/payment method, receipts/payments/refunds matching, and discrepancy drill-downs.
   - **App 05 — Forecasting & Anomaly Engine:** Short-horizon time-series forecasting (`statsmodels` ETS/ARIMA with 95% CI and temporal walk-forward validation) and anomaly alerts.

---

## Technology Stack

| Layer | Technology |
|---|---|
| **Database** | PostgreSQL 16 (Docker Compose) |
| **Core Language** | Python 3.12 (Conda environment `hvia_task04`) |
| **ORM & Drivers** | SQLAlchemy 2.0, `psycopg2-binary`, `asyncpg` |
| **Data Processing & DQ** | Pandas, NumPy, SciPy, Pandera, Pydantic, RapidFuzz |
| **Arabic Text Support** | Python `unicodedata`, `arabic-reshaper`, `python-bidi` |
| **Analytics & ML** | `statsmodels`, `scikit-learn` |
| **BI Dashboards** | Streamlit, Plotly, Matplotlib, Seaborn |
| **Local AI** | Ollama (`qwen3.5:4b`) via `ollama` / `httpx` |
| **Testing & Linting** | `pytest`, `pytest-cov`, `pytest-asyncio`, `pytest-mock`, `ruff` |

---

## Repository Structure

```text
HVIA-intern-project4/
├── .gitignore
├── README.md
├── requirements.txt
├── docker-compose.yml              # PostgreSQL 16 service
├── .env.example                    # Environment variable template
├── config/                         # Settings & centralized rotating logging
├── db/                             # Connection pooling & SQLAlchemy ORM models
├── etl/                            # Dump restoration & raw -> staging -> analytics ETL
├── data_quality/                   # Profiler, diagnoser, repairer, validator, reporter & rules
├── ai/                             # Local Ollama client, prompt templates & DQ advisor
├── analytics/                      # KPIs, pricing, RFM, retention, treasury, forecasting, anomaly
├── dashboard/                      # Multi-page Streamlit app & reusable Plotly components
├── tests/                          # Unit, integration, DQ, ETL, ML & negative tests
├── docs/                           # Architecture, schema, ETL, DQ & user documentation
├── logs/                           # Rotating log files (gitignored)
└── reports/                        # Generated DQ & validation reports (gitignored)
```

---

## Environment Setup

### 1. Create and Activate the Conda Environment

```bash
conda create -n hvia_task04 python=3.12 -y
conda activate hvia_task04
pip install -r requirements.txt
# Or for exact reproducible versions:
# pip install -r requirements.lock.txt
```

### 2. Configure Environment Variables

```bash
cp .env.example .env
# Edit .env with your actual PostgreSQL password (use POSTGRES_PORT=5433 if a local PostgreSQL occupies 5432)
```

### 3. Start PostgreSQL

```bash
docker compose up -d postgres
```

### 4. Restore the Database

```bash
python -m etl.restore
# or with a specific dump file:
python -m etl.restore path/to/dump.sql
# explicit destructive raw rebuild only with confirmation flag:
python -m etl.restore --rebuild-raw --confirm
```

### 5. Verify Local Ollama Setup

Ensure the local Ollama service is running and that only local models are configured:

```bash
ollama list
```

---

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Integration tests only (requires running PostgreSQL container)
pytest tests/ -v -m integration

# Unit tests only
pytest tests/ -v -m "not integration"

# With coverage
pytest --cov=. --cov-report=term-missing
```

---

## Project Status

- **Phase 1 (Foundation & Infrastructure):** `IN_PROGRESS`
  - P1-T01 Repository Hygiene — ✅ Done
  - P1-T02 Docker + Conda — ✅ Done
  - P1-T03 Settings & Logging — ✅ Done
  - P1-T04 SQL Restore & Raw Schema — ✅ Done
  - P1-T05 Database Connection Pool — 🔄 In Progress
  - P1-T06 ORM Models — Pending
  - P1-T07 Test Infrastructure — Pending
- **Phase 2–6:** Pending — See implementation plan.
