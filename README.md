# Idempotent Payment Transfer Backend

A wallet-to-wallet transfer backend (FastAPI + PostgreSQL) built version by version to understand **why** payment systems need transactions, row locks and idempotency.

Version 0 is deliberately naive: no locks, no transactions around transfers. The race-condition tests are written first and are **expected to fail**, so each bug is seen before it is fixed. Every later version is judged by the same tests turning green.

> Both accounts live inside this system. There is no external bank. A fake external gateway comes in v4.

## Version ladder

| Version | What changes | Race tests |
|---|---|---|
| **v0** (done) | Naive accounts, deposit, withdraw, transfer. No locks. | 3 fail, on purpose |
| v1 | One DB transaction per transfer, row lock, `CHECK(balance >= 0)` | should pass |
| v2 | Idempotency key + `UNIQUE` constraint, API in Docker with 2 replicas | double-debit test |
| v3 | Request hash, saved response, `409` (in progress) / `422` (same key, different body) | |
| v4 | Fake PSP, crash recovery (`recovery_point`, stuck `in_progress`, lock TTL) | |
| v5 | Double-entry ledger, reconciliation, load test, CI | |

## v0: what the race tests show

| Test | Expected | v0 result | Why |
|---|---|---|---|
| `test_lost_update` (50 parallel deposits of 100) | balance 5000 | far less (100 to 200 in runs) | every task reads the old balance and writes a full new number, the last commit wins |
| `test_negative_balance` (10 parallel withdrawals of 100 from a balance of 100) | 1 success, 9 rejected | 10 successes | the balance check and the update are not atomic (check-then-act) |
| `test_crash_midway` (credit fails after debit) | sender keeps 500 | sender has 300 | debit and credit are separate commits, the transfer row stays `PENDING` |

The numbers change from run to run because they depend on timing. The fixed part is that the expected value is never reached.

The structured logs carry the proof: each `balance_changed` line records `balance_read` and `balance_written`, and many lines show the same `balance_read`.

## Stack

- Python, FastAPI, Pydantic
- PostgreSQL 17 in Docker (host port `5433`)
- SQLAlchemy 2.0 async ORM with asyncpg
- Alembic migrations (tables are created only through Alembic)
- structlog (JSON logs to stdout, one `request_id` per request)
- pytest, pytest-asyncio

## Project layout

```
app/
  config.py          settings from .env (pydantic-settings)
  database.py        async engine, session factory, get_db dependency
  models.py          User (wallet) and Transfer tables
  schemas.py         request and response shapes
  crud.py            all DB work (accounts, deposit, withdraw, transfer)
  exceptions.py      AccountNotFound, InsufficientBalance, BalanceNotZero, InvalidTransfer
  logging_config.py  structlog setup
  main.py            routes, exception handlers, request logging middleware
alembic/             migrations
tests/
  conftest.py        test DB fixtures
  test_accounts.py   28 normal tests
  test_race_v0.py    race tests (expected to fail in v0)
docker-compose.yml
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

copy .env.example .env        # then fill in the values
docker compose up -d          # Postgres on port 5433
python -m alembic upgrade head # create tables
python -m uvicorn app.main:app --reload --no-access-log
```

Open `http://localhost:8000/docs` for the interactive API.

`.env` variables (see `.env.example` for the exact names): database host, port, user, password, database name, test database name and log level.

## API

| Method | Path | What it does |
|---|---|---|
| POST | `/accounts` | create an account (balance starts at 0) |
| GET | `/accounts` | list active accounts |
| GET | `/accounts/{id}` | account info and balance |
| PATCH | `/accounts/{id}` | change the name only |
| DELETE | `/accounts/{id}` | soft delete, allowed only if balance is 0 |
| POST | `/accounts/{id}/deposit` | add money |
| POST | `/accounts/{id}/withdraw` | take money out |
| POST | `/transfers` | move money from one account to another |
| GET | `/health` | API and DB check |

Errors: `404` account not found, `409` balance must be zero to close, `400` insufficient balance or invalid transfer, `422` request body failed validation.

## Design decisions

- **Money is an integer** (whole rupees), never a float.
- **ATM model:** balance changes only through deposit, withdraw or transfer, never through `PATCH`. A new account starts at 0.
- **Soft delete:** `deleted_at` is set instead of removing the row, so old transfers keep their foreign keys. Deleted accounts do not show up in reads.
- **Transfer history:** a `transfers` table with `sender_id`, `receiver_id`, `amount`, `status` (`PENDING`, `SUCCESS`, `FAILED`) and `created_at`.
- **Validation twice:** amount must be above 0 in the Pydantic schema (`422`) and again in `crud.py` (`InvalidTransfer`), because transfer logic can be called without the API.
- **Exceptions, not strings:** crud raises named exceptions and global handlers turn them into HTTP status codes.
- **Auth is a later layer.** It does not change the transfer logic, only who is allowed to call it.

## Logging

JSON lines on stdout, every line has a `request_id`. Each line also has a `log_type`:

| log_type | Meaning |
|---|---|
| REQUEST | one line per request (method, path, status, duration_ms) |
| BUSINESS | normal events (`account_created`, `transfer_created`) |
| REJECT | a business rule refused the action (warning) |
| AUDIT | money moved: when, who, how much |
| DEBUG-EVIDENCE | balance read and balance written, for race debugging (debug level only) |
| ERROR | unexpected failures |

Secrets (passwords, `DATABASE_URL`) are never logged.

## Tests

Tests run against a separate database in the same Docker Postgres, so development data is never touched. Create it once:

```powershell
docker compose exec postgres psql -U <DB_USER> -d <DB_NAME> -c "CREATE DATABASE idempotent_test;"
```

Run:

```powershell
python -m pytest -v                          # everything
python -m pytest tests/test_accounts.py -v   # 28 normal tests, all pass
python -m pytest tests/test_race_v0.py -v    # race tests, fail in v0 by design
```

Each test starts from freshly created empty tables (`drop_all` then `create_all`), and `conftest.py` refuses to run if the test database name does not contain `test`.

## Next

v1: wrap debit, credit and status update in one transaction with a row lock (`SELECT ... FOR UPDATE`) and add `CHECK(balance >= 0)`. Then run the same race tests again. They should pass.
