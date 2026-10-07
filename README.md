# RMA — Account Statement API, Digital RM Twin Backend & MCP Server

Master context document for this project. Read this first in any new session
before touching code — it captures architecture, decisions, and progress
across steps so work can resume without re-deriving context.

> **Data sensitivity note:** this project holds one dataset in the
> same `data/rma.db` file, kept apart by account number:
> - **Account `ACC101`** — entirely **fictional** demo data (a
>   "Digital RM Twin" proof-of-concept script: a fictional customer "Mr.
>   Mehta", a disputed card transaction, a card-upgrade pitch). Seeded by
>   `etl/seed_demo_data.py`, deliberately kept under its own account number
>   so it can never mix with or corrupt the real `8552` rows.

## Project goal


**Chapter 2 — Digital RM Twin backend (done, Steps 1–10)**
Built from `Digital RM Twin_Demo Script.docx`, a demo of an AI bank RM
video-call assistant. 



### Chapter summary



## API reference

### REST (`api/main.py`) — full spec in `openapi.json`

| Method | Path | Added in |
|---|---|---|
| GET | `/accounts/{account_number}/transactions` | Chapter 1 |
| GET | `/accounts/{account_number}/cards` | Step 3 |
| GET | `/cards/{card_id}` | Step 3 |
| GET | `/card-products` | Step 3 |
| GET | `/card-products/{card_product_id}` | Step 3 |
| GET | `/cards/{card_id}/transactions` | Step 4 |
| GET | `/cards/{card_id}/forex-summary` | Step 4 |
| GET | `/cards/{card_id}/category-breakdown` | Step 4 |
| GET | `/accounts/{account_number}/customer` | Step 5 |
| PATCH | `/customers/{customer_id}/delivery-preference` | Step 5 |
| POST | `/transactions/{transaction_id}/disputes` | Step 6 |
| POST | `/disputes/{dispute_id}/withdraw` | Step 6 |
| GET | `/cards/{card_id}/recommendation` | Step 7 |
| POST | `/card-applications` | Step 8 |
| GET | `/card-applications/{application_id}` | Step 8 |



### MCP tools (`mcp_server/server.py`)

`get_account_txn_details`, `list_account_cards`, `list_card_products`,
`get_card_product`, `search_account_transactions`, `get_account_forex_summary`,
`get_account_category_breakdown`, `get_customer_profile`, `get_customer_360`,
`get_latest_service_request`, `update_customer_delivery_preference`,
`create_dispute`, `withdraw_dispute`, `get_account_recommendation`,
`create_card_application`, `get_card_application_status`.



## Architecture

```
C:\RMA\
  README.md                   <- this file
  requirements.txt
  openapi.json                 # exported Swagger/OpenAPI 3.1.0 spec
  config/
    settings.py                # paths, DATABASE_URL (env-overridable), no secrets
    logging_config.py          # one-time logging setup (console + rotating file)
  db/
    models.py                  # SQLAlchemy ORM: all 9 tables (see data model below)
    session.py                 # engine, SessionLocal, init_db() (+ auto column-migration)
  etl/
    excel_importer.py          # parses the real .xls statement into SQLite (account 8552)
    seed_demo_data.py          # seeds the fictional RM Twin demo data (account ACC101)
  api/
    schemas.py                 # Pydantic response/request models
    repository/                # framework-agnostic query/write logic, split by domain
      __init__.py                # re-exports everything (callers' imports unaffected)
      transactions.py            # getAccountTxnDetails (Chapter 1)
      merchants.py                # resolve_merchant (Step 2)
      cards.py                    # catalogue, search, forex summary, category, recommendation (Steps 3/4/7)
      customers.py                 # profile, delivery preference (Step 5)
      disputes.py                  # dispute lifecycle (Step 6)
      applications.py              # card application lifecycle (Step 8)
    dependencies.py             # FastAPI get_db() session dependency
    main.py                     # FastAPI app + all HTTP routes
  mcp_server/
    server.py                   # all 15 MCP tools + SSE server entry point
  tests/                        # 143 tests, 3-layer pattern per feature:
                                 #   repository (function + injected session_factory),
                                 #   API (FastAPI TestClient), MCP (call_tool() in-process)
  data/
    Account_Statement_Sep26.xls  # real statement source (account 8552)
    rma.db                       # SQLite -- holds BOTH 8552 (real) and ACC101 (fictional)
  logs/
    app.log
```

### Design decisions and why

- **SQLAlchemy ORM**, not raw `sqlite3` — typed models, migrations headroom,
  swap-in path to Postgres later.
- **`Numeric`/`Decimal` for money**, never `float` — avoids rounding drift.
- **`Account` is a real table** — lets later steps validate account numbers
  and hold per-account metadata.
- **Card transactions merge into the existing `transactions` table** rather
  than a parallel ledger — a debit card hits the same account balance this
  table already tracks; a credit card (no issued ledger yet) only needs a
  `card_products`/`card_applications` row.
- **`api/repository/` is a package, split by domain** — ~20 functions across
  7+ tables would make one file unwieldy; `__init__.py` re-exports so no
  caller's import needed to change.
- **Emails are returned unmasked** by `CustomerOut` and `Customer360Out`
  (masking was removed deliberately; access is gated by the bearer token).
- **Merchant resolution is deterministic** (exact alias match, then
  `difflib` fuzzy fallback) — no external enrichment vendor, appropriate for
  a demo dataset built from a fixed script.
- **Recommendation/application fee math derives its GST rate from the
  card's own data**, not a hardcoded constant — stays internally consistent
  with whatever real or seeded data backs it.
- **Dedup constraints + skip-on-duplicate** throughout (`uq_txn_dedup`, card
  identity, merchant identity/alias, one customer per account, card-product
  name) — every importer/seeder is safe to re-run.
- **`init_db()` auto-adds missing columns** on top of `create_all()` — the
  project has no Alembic; this closes the gap for the common case (a new
  nullable column on an existing table) without adding a dependency.
- **`RMA_DATABASE_URL` env var override** — any component can point at a
  different database without code changes.


