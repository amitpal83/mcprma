# RMA — Account Statement API, Digital RM Twin Backend & MCP Server

Master context document for this project. Read this first in any new session
before touching code — it captures architecture, decisions, and progress
across steps so work can resume without re-deriving context.

> **Data sensitivity note:** this project holds TWO separate datasets in the
> same `data/rma.db` file, kept apart by account number:
> - **Account `8552`** — the owner's own **real** HDFC bank statement
>   (imported from `data/Account_Statement_Sep26.xls`). Treat this as
>   sensitive: don't commit `data/*.xls`/`data/rma.db` to a shared/public
>   repo, don't paste their contents into external tools, don't send them
>   anywhere outside this machine.
> - **Account `ACC101`** — entirely **fictional** demo data (a
>   "Digital RM Twin" proof-of-concept script: a fictional customer "Mr.
>   Mehta", a disputed card transaction, a card-upgrade pitch). Seeded by
>   `etl/seed_demo_data.py`, deliberately kept under its own account number
>   so it can never mix with or corrupt the real `8552` rows.

## Project goal

Two chapters, each step approved before moving to the next:

**Chapter 1 — Account statement API (done)**
1. Load an Excel bank statement into SQLite (`etl/excel_importer.py`).
2. Expose it via an API: `getAccountTxnDetails(account_number, from_date, to_date) -> list[Transaction]`.
3. Wrap that API as an MCP server over SSE (local build/test only — see
   policy note below).

**Chapter 2 — Digital RM Twin backend (done, Steps 1–10)**
Built from `Digital RM Twin_Demo Script.docx`, a demo of an AI bank RM
video-call assistant. The script needed backend support for: recognizing a
debit-card forex transaction whose statement descriptor ("WISDOM PROPERTY NL
II") differs from its real merchant (DoubleTree by Hilton Amsterdam);
showing 12-month forex spend/markup on that card; recommending a
zero-forex credit card with a relationship-tier fee discount; a
dispute-then-withdraw flow; and a card application with a delivery
preference. See `openapi.json` / **API reference** below for the full
surface this added.

## Current status: both chapters complete, live-smoke-tested

- Chapter 1: Steps 1–3 done (see "Chapter 1 detail" below) — unchanged since
  before Chapter 2's work.
- Chapter 2: Steps 1–10 done, approved one step at a time, each with its own
  tests. **143/143 tests passing.** A live smoke test (Step 10) ran the real
  FastAPI server and MCP server against the real `data/rma.db`, replayed the
  entire demo script end-to-end via real HTTP/MCP calls, and confirmed the
  exact figures the script quotes (₹3,80,000 forex spend, ₹15,694 markup+GST,
  ₹13,275 discounted joining fee) — see "Verification record" below.
- OpenAPI/Swagger spec exported to `openapi.json` (24KB, OpenAPI 3.1.0),
  generated directly from the live app so it can't drift from the code.

### Chapter 2 step-by-step summary

1. **Schema** (`db/models.py`): `Transaction` gained 10 nullable columns
   (`card_id`, `merchant_id`, `txn_currency`, `txn_amount`, `exchange_rate`,
   `forex_markup_pct`, `forex_markup_amount`, `gst_on_markup`, `mcc`,
   `category`) so a debit-card transaction is just a `transactions` row with
   those populated — no parallel ledger, since a debit card already hits the
   same account balance this table tracks. 7 new tables: `card_products`
   (catalogue), `cards` (issued instances), `merchants`/`merchant_aliases`
   (statement-descriptor → brand resolution), `customers` (RM-facing
   profile), `disputes`, `card_applications`. Credit cards (the recommended
   upgrade) get no transaction ledger — only a `card_products` row and a
   `card_applications` row, since the customer doesn't have one yet.
2. **Merchant resolution** (`api/repository/merchants.py`): `api/repository.py`
   became a package, split by domain (`transactions.py`, `merchants.py`,
   `cards.py`, `customers.py`, `disputes.py`, `applications.py`, re-exported
   from `__init__.py`). `resolve_merchant()`: exact alias match first,
   `difflib` fuzzy fallback (`FUZZY_MATCH_THRESHOLD = 0.6`) — deterministic,
   no external enrichment vendor.
3. **Card & card-product catalogue** (`api/repository/cards.py`): read-only
   lookups + `CardOut`/`CardProductOut` schemas (the latter JSON-decodes
   `reward_transfer_partners` so API/MCP consumers see a real `list[str]`,
   never raw JSON text).
4. **Card-transaction search, forex summary, category breakdown**
   (`api/repository/cards.py`, continued): `search_card_transactions()`
   (amount range matches either currency; merchant-text resolves via Step 2
   then falls back to a narration substring match), `get_card_forex_summary()`
   (trailing-365-day window), `get_card_category_breakdown()`. **Caught a
   real MCP SDK bug here**: a bare `-> dict` return annotation gets no
   structured-output schema at all; fixed by annotating single-object tools
   `-> dict[str, Any]` (which, unlike `list[dict]`, is *not* wrapped under
   `{"result": ...}`).
5. **Customer profile, PII masking, delivery preference**
   (`api/repository/customers.py`): `mask_email()` masks only the local part,
   keeps the domain visible (matches the demo script's real-time masking
   beat). Masking lives in the **schema layer** (`CustomerOut`) via a
   `model_validator(mode="before")` — the raw email is not a field on that
   model at all, so it structurally cannot leak into a response.
6. **Disputes** (`api/repository/disputes.py`): `create_dispute`/
   `withdraw_dispute` — first use of HTTP 409 in this codebase (duplicate
   open dispute / re-withdraw).
7. **Rules-based recommendation** (`api/repository/cards.py`, continued):
   `recommend_card_upgrade()` — lowest-forex-markup active credit product,
   projected savings using the *current card's own derived GST rate* (not a
   hardcoded one), relationship-tier-gated discount. Verified against the
   demo script's own numbers (see below).
8. **Card applications** (`api/repository/applications.py`): reuses Step 7's
   discount/GST logic so the fee a customer applies at matches what the RM
   quoted. Duplicate `SUBMITTED` application blocked; a new one is allowed
   once a prior one is `REJECTED`/`CANCELLED`.
9. **Seed data** (`etl/seed_demo_data.py`): seeds the fictional dataset under
   `ACC101` (see data-sensitivity note above) — DoubleTree/Wisdom
   Property merchant+alias, both card products, Mr. Mehta's customer profile,
   his debit card (`•••• 4821`), and 15 forex transactions summing to exactly
   the script's figures. Idempotent re-run, same dedup-by-natural-key pattern
   as `etl/excel_importer.py`.
10. **Full verification**: automated suite (143 tests) + a live smoke test
    against the real `data/rma.db` (see below).

### A real bug the live smoke test caught (and fixed)

`data/rma.db` predated Step 1's schema changes. `init_db()`'s
`Base.metadata.create_all()` only creates **missing tables** — it never adds
new **columns** to a table that already exists, so seeding failed with
`no such column: transactions.card_id`. No throwaway-db test could catch
this (every test starts from a blank schema). Fixed in `db/session.py`:
`init_db()` now diffs each model's declared columns against the actual table
and issues `ALTER TABLE ADD COLUMN` for anything missing — additive only,
safe to call repeatedly, with its own regression tests
(`tests/test_init_db_migration.py`). Still no real migration tool (no
Alembic) — this only covers the "new nullable column" case; a rename, a
dropped column, or a new `NOT NULL` column with no default still needs one.

### Verification record (live smoke test, Step 10)

Ran the real `uvicorn` server and the real MCP SSE server against the real
`data/rma.db`, confirmed:

| Script beat | Result |
|---|---|
| €353 "Wisdom Property" on 12 Aug | Found via amount+merchant-text search; resolves to DoubleTree by Hilton Amsterdam |
| "~₹3.8 lakh forex spend... ~₹15,700 extra" | `total_forex_spend_inr: 380000.00`, `total_markup_and_gst: 15694.00` |
| "₹11,250 + GST — ₹13,275 including GST" | `net_joining_fee_after_discount` AND actual `fee_charged` both `13275.00` |
| Dispute raised then withdrawn | `OPEN` → duplicate correctly `409` → `WITHDRAWN` |
| Card application submitted | `201`, discount + fee computed correctly |
| Masked email / office delivery preference | `"meh***@gmail.com"`, `"ram*****@outlook.com"`, `preferred_delivery_address_type: "OFFICE"` |
| 404 on unknown resources | Confirmed on both REST and MCP |

Confirmed the real `8552` account's 20 original transactions were untouched
throughout (same narrations/amounts, `card_id` correctly `NULL`).

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

View interactively: `python -m uvicorn api.main:app --reload` then
`http://127.0.0.1:8000/docs` (Swagger UI) or `/redoc`. Regenerate the static
spec with:
```bash
python -c "import json; from api.main import app; json.dump(app.openapi(), open('openapi.json','w'), indent=2)"
```

### MCP tools (`mcp_server/server.py`) — one-to-one with the REST routes above

`get_account_txn_details`, `list_account_cards`, `get_card`,
`list_card_products`, `get_card_product`, `search_card_transactions`,
`get_card_forex_summary`, `get_card_category_breakdown`,
`get_customer_profile`, `update_customer_delivery_preference`,
`create_dispute`, `withdraw_dispute`, `get_card_recommendation`,
`create_card_application`, `get_card_application_status`.

Each follows the same two-layer pattern: a transport-independent `fetch_*()`
function (testable with an injected `session_factory`) plus a thin
`@mcp.tool()` wrapper that logs and delegates. Domain exceptions become
`ToolError` at the boundary.

**Policy note (BCG) — repeated because it matters:** this server is a local
build/test artifact only. MCP servers must be on the BCG-approved list (CT
GenAI Workspace Squad) before being registered against real data or
connected to PortKey. Get that sign-off before wiring this into PortKey.

### Chapter 1 detail — `getAccountTxnDetails` API & MCP tool

- Source file: `data/Account_Statement_Sep26.xls` (HDFC Bank statement
  export). Per user instruction, only the first 20 transactions were
  imported, under account `8552` (user-supplied; the file has no account
  number column).
- Core query logic: `api/repository/transactions.py::get_account_txn_details()`
  — framework-agnostic, raises `AccountNotFoundError`/`InvalidDateRangeError`.
  Exposed over HTTP (`GET /accounts/{account_number}/transactions`) and as
  the MCP tool `get_account_txn_details`.

### Source file layout (as discovered)

Row 0 of `Account_Statement_Sep26.xls` is the header row with these exact
column names:

| Column | Notes |
|---|---|
| `Date` | `DD/MM/YY`, e.g. `01/09/26` → 2026-09-01 |
| `Narration` | Free text, UPI/IMPS/ACH descriptions |
| `Chq./Ref.No.` | Bank reference number, kept as text (leading zeros matter) |
| `Value Dt` | Same format as `Date` |
| `Withdrawal Amt.` | Blank (NaN) when the row is a credit |
| `Deposit Amt.` | Blank (NaN) when the row is a debit |
| `Closing Balance` | Always present |

Rows after the transaction table (from row ~62 onward) are bank-generated
footer content and are excluded by the importer.

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
      customers.py                 # profile, mask_email, delivery preference (Step 5)
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
- **PII masking lives in the schema layer**, not the repository — repository
  returns the full ORM row (so any future internal-only consumer still has
  it); `CustomerOut` computes masked fields and never exposes the raw ones.
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

## How to run things

All commands assume the working directory is `C:\RMA` and dependencies are
installed:

```bash
python -m pip install -r requirements.txt
```

**Run the real-data importer** (account `8552`, idempotent):
```bash
python -m etl.excel_importer data/Account_Statement_Sep26.xls 8552 --max-rows 20
```

**Seed the fictional RM Twin demo data** (account `ACC101`, idempotent):
```bash
python -m etl.seed_demo_data
```

**Run tests:**
```bash
python -m pytest tests/ -v
```

**Run the API locally:**
```bash
python -m uvicorn api.main:app --reload
# then browse http://127.0.0.1:8000/docs, or:
curl "http://127.0.0.1:8000/cards/1/forex-summary"
```

**Run the MCP server locally:**
```bash
python -m mcp_server.server
# SSE endpoint: http://127.0.0.1:8001/sse (override with MCP_HOST / MCP_PORT)
```

**Inspect the database directly:**
```bash
python -c "import sqlite3; c=sqlite3.connect('data/rma.db'); print(c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall())"
```

## Data model reference

```
accounts
  account_number   TEXT PRIMARY KEY
  display_name     TEXT NULL
  created_at       TIMESTAMP

transactions
  id                 INTEGER PRIMARY KEY AUTOINCREMENT
  account_number     TEXT NOT NULL   FK -> accounts.account_number
  txn_date           DATE NOT NULL
  value_date         DATE NOT NULL
  narration          TEXT NOT NULL   -- also the raw card merchant descriptor, for card rows
  reference_no       TEXT NULL
  withdrawal_amount  NUMERIC(18,2) NULL   -- INR-settled amount
  deposit_amount     NUMERIC(18,2) NULL
  closing_balance    NUMERIC(18,2) NOT NULL
  created_at         TIMESTAMP
  card_id             INTEGER NULL   FK -> cards.id          (NULL = plain bank-narration row)
  merchant_id         INTEGER NULL   FK -> merchants.id       (enrichment result)
  txn_currency         TEXT(3) NULL   (NULL = home currency)
  txn_amount            NUMERIC(18,2) NULL   (original foreign-currency amount)
  exchange_rate         NUMERIC(12,6) NULL
  forex_markup_pct      NUMERIC(5,2) NULL
  forex_markup_amount   NUMERIC(18,2) NULL
  gst_on_markup          NUMERIC(18,2) NULL
  mcc                     TEXT(4) NULL
  category                 TEXT(50) NULL

  UNIQUE (account_number, reference_no, txn_date)
  INDEX  (account_number, txn_date)
  INDEX  (card_id, txn_date)

card_products                                merchants
  id PK                                         id PK
  name UNIQUE                                   brand_name
  network, card_type (debit/credit)             mcc, category, sub_category
  forex_markup_pct                              associated_property
  joining_fee, annual_fee                       city, country
  lounge_visits_domestic/international_per_year  UNIQUE(brand_name, city)
  guest_visits_per_year
  reward_transfer_partners (JSON text)         merchant_aliases
  min_relationship_tier_for_discount             id PK
  relationship_discount_pct                      merchant_id FK
  is_active                                      raw_pattern UNIQUE (normalized)
                                                  match_type
cards
  id PK                                        customers
  account_number FK                              id PK
  card_product_id FK                             account_number FK, UNIQUE
  last4, network, card_type, status              full_name
  issued_at                                      registered_email, alt_email (never serialized raw)
  UNIQUE(account_number, last4, network)         relationship_tier
                                                  delivery_address_office/home
disputes                                        preferred_delivery_address_type
  id PK
  transaction_id FK                            card_applications
  status (OPEN/WITHDRAWN)                         id PK
  reason, raised_at, resolved_at                 customer_id FK, card_product_id FK
                                                  status (SUBMITTED/APPROVED/REJECTED/CANCELLED)
                                                  applied_at
                                                  discount_pct_applied, fee_charged
                                                  delivery_address (snapshot, not a live FK)
```

## Next steps

- Register the MCP server and connect it to PortKey — **blocked on BCG
  IT/Security approval** (MCP servers must be on the BCG-approved list
  maintained by the CT GenAI Workspace Squad before being registered against
  real data or connected to PortKey).
- All code here is a working backend built quickly through an iterative,
  step-approved process — **review and test independently** before treating
  any of it as final, per the reminder given at the end of Step 10.

## Environment

- Windows, no WSL2/sandbox confirmed — working directory discipline and
  manual command review are the operating compensating controls for this
  project (per BCG Claude Code safeguards).
- Python 3.14, dependencies pinned loosely in `requirements.txt`
  (pandas, xlrd, openpyxl, SQLAlchemy, pytest, fastapi, uvicorn, httpx, mcp).
