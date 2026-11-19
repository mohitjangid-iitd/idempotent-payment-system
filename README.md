# Idempotent Payment Transfer Backend

A wallet-to-wallet transfer backend (FastAPI + PostgreSQL) built version by version to understand **why** payment systems need transactions, row locks and idempotency.

Version 0 is deliberately naive: no locks, no transactions around transfers. The race-condition tests are written first and are **expected to fail**, so each bug is seen before it is fixed. Every later version is judged by the same tests turning green.

> Both accounts live inside this system. There is no external bank. A fake external gateway comes in v4.

## Version ladder

| Version | What changes | Race tests |
|---|---|---|
| **v0** (done, tag `v0-naive`) | Naive accounts, deposit, withdraw, transfer. No locks. | 3 fail, on purpose |
| **v1** (done, tag `v1-transactions`) | Atomic transfer with rollback, row lock, fixed lock order, `CHECK(balance >= 0)` | all pass, plus a new deadlock test |
| **v2** (done, tag `v2-idempotency`) | `Idempotency-Key` header + `UNIQUE(sender_id, key)`, duplicate returns the first transfer. API in Docker, 2 replicas behind nginx | double-click test passes, also across 2 replicas |
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

## v1: what changed and what the tests show

The same race tests that failed in v0 now pass, and one new test found a gap that the first three did not cover.

| Test | v0 | v1 | Fix |
|---|---|---|---|
| `test_lost_update` (50 parallel deposits) | balance far below 5000 | 5000 | row lock: `SELECT ... FOR UPDATE`, so each task reads the balance the previous one committed |
| `test_negative_balance` (10 parallel withdrawals from 100) | 10 successes | 1 success, 9 rejected | same row lock, the balance check and the update are now one locked step |
| `test_crash_midway` (credit fails after debit) | sender stuck at 300, row `PENDING` | sender back at 500, row `FAILED` | debit, credit and status update are one transaction, any failure rolls it all back |
| `test_opposite_transfers_no_deadlock` (20 transfers A to B and 20 B to A in parallel) | not tested | 38 of 40 died with `deadlock detected` before the fix, 0 errors after | both accounts are locked in sorted id order, so every transfer takes the same path |
| `test_db_rejects_negative_balance` (raw SQL `UPDATE ... SET balance = -1`) | not tested | `IntegrityError` | `CHECK (balance >= 0)` in the database, the last line of defense under the app's own check |

How a transfer runs now:

1. Validate the request. Nothing is written if the input is bad.
2. Insert the transfer row as `PENDING` and commit, so the transfer has a stable id from the start.
3. In one transaction: lock both accounts in sorted id order, debit, credit, set `SUCCESS`, commit once.
4. If anything fails: roll back, then mark the same row `FAILED` in a short separate transaction, so the user can still see the failed transfer.

Details worth knowing:

- `get_user_for_update` uses `populate_existing=True`. Without it SQLAlchemy can hand back an object it already holds, with a stale balance, and the lock would protect nothing.
- A lock is released on commit or rollback, so a lock taken before the `PENDING` commit protects nothing. The real locks are taken after it.
- After a rollback, session objects are expired. Ids are saved into plain variables before it.
- The application check (`InsufficientBalance`) gives a clear error. The `CHECK` constraint is the safety net: the database stays correct even if application code is wrong.

Known gap: if marking the row `FAILED` itself fails (for example the database goes down), the row stays `PENDING`. Recovering from that is v4 (crash recovery).

## v2: idempotency (what changed)

The same transfer sent twice (retry after a lost response, double click) used to move money twice. Now the client sends an `Idempotency-Key` header, and a repeated key returns the first transfer instead of moving money again.

| Test | Before | v2 |
|---|---|---|
| retry, same key twice | 2 transfers, money moved twice | 1 transfer, same id returned, money moved once |
| different keys | n/a | 2 separate transfers (no over-deduplication) |
| double click (20 parallel requests, same key) | 20 transfers | 1 transfer, no errors, all 20 get the same id |
| API: same header twice | n/a | first `201`, second `200`, one row, balance debited once |
| API: header missing | n/a | `422`, nothing written |

How it works:

1. `transfers.idempotency_key` (nullable `varchar(64)`) and `UNIQUE(sender_id, idempotency_key)` (`uq_transfer_sender_key`), added by an Alembic migration.
2. `transfer()` inserts the `PENDING` row and commits. If the database raises `IntegrityError`, the key was already used: roll back, look up the old transfer by `sender_id` and key, and return it. No debit or credit runs.
3. If the key is `None`, or no old row is found, the `IntegrityError` is re-raised, because it was some other error.
4. `crud.transfer()` returns `(transfer, created)`. The endpoint answers `201` when created and `200` for a duplicate.

### v2: running in Docker with 2 replicas

The API runs as 2 containers behind nginx, all talking to the same Postgres. This proves that idempotency lives in the database (`UNIQUE` constraint), not in one process's memory.

```
client -> nginx (port 8000) -> api x2 -> postgres
```

- `Dockerfile` builds the API image, `docker-compose.yml` runs `api` (2 replicas), `nginx` and `postgres`.
- Inside a container `localhost` is the container itself, so compose overrides `DB_HOST=postgres` and `DB_PORT=5432` for `api`. The local `.env` (`localhost:5433`) is unchanged and still used by pytest and Alembic.
- `check_concurrent.py` sends 20 parallel requests with the same key through nginx. Result: one `201`, nineteen `200`, one transfer id, money moved once.

Run the whole stack:

```powershell
docker compose up -d --build
python check_concurrent.py      # needs the stack running
```

Design decisions:

- **The key is made by the client** (one UUID per intent, created when the payment screen opens, not on every click). The server never guesses a duplicate from amount or users, otherwise paying the same person the same amount twice on purpose would be blocked.
- **The unique constraint includes `sender_id`**, so two senders who happen to use the same key do not collide, and one sender never sees another sender's transfer.
- **The header is required on the API** (missing means `422`), because a money-moving retry without a key is exactly the double debit this version removes. `crud.transfer()` still accepts `None` for internal callers.
- **Postgres treats `NULL` as distinct inside `UNIQUE`**, so rows without a key never conflict with each other.
- **The database is the saved result.** The old `transfers` row is what a retry gets back. Redis could later make this faster, but correctness stays in Postgres.

Known gaps (planned for v3):

- A duplicate that arrives while the first request is still running gets the transfer with status `PENDING`. v3 will answer `409` (in progress).
- A retry of a `FAILED` transfer with the same key returns the `FAILED` row. A new attempt needs a new key.
- Same key with a different body is not detected yet. v3 adds a request hash and `422`.
- Validation runs before the key lookup, so a retry after the receiver was deleted gets `404` instead of the old result.

## Stack

- Python, FastAPI, Pydantic
- PostgreSQL 17 in Docker (host port `5433`)
- SQLAlchemy 2.0 async ORM with asyncpg
- Alembic migrations (tables are created only through Alembic)
- structlog (JSON logs to stdout, one `request_id` per request)
- pytest, pytest-asyncio, httpx

## Project layout

```
app/
  config.py          settings from .env (pydantic-settings)
  database.py        async engine, session factory, get_db dependency
  models.py          User (wallet) and Transfer tables (with idempotency_key)
  schemas.py         request and response shapes
  crud.py            all DB work (accounts, deposit, withdraw, transfer)
  exceptions.py      AccountNotFound, InsufficientBalance, BalanceNotZero, InvalidTransfer
  logging_config.py  structlog setup
  main.py            routes, exception handlers, request logging middleware
alembic/             migrations
tests/
  conftest.py        test DB fixtures (engine, session_factory, make_user, client)
  test_accounts.py   28 normal tests
  test_race_v0.py    race tests (failed in v0 by design, pass since v1)
  test_constraints.py  database-level CHECK constraint test
  test_idempotency.py  retry, different keys, double click, API header tests
docker-compose.yml   postgres, api (2 replicas), nginx
Dockerfile
nginx.conf
check_concurrent.py  parallel idempotency check against the running stack
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env        # then fill in the values
docker compose up -d          # Postgres on port 5433
python -m alembic upgrade head          # create tables
python -m uvicorn app.main:app --reload --no-access-log
```

On Windows, running `uvicorn`, `alembic` or `pytest` directly can be blocked by Application Control ("Application Control policy has blocked this file"). Starting them as `python -m <tool>` avoids that.

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
| POST | `/transfers` | move money from one account to another. Needs an `Idempotency-Key` header (max 64 chars). `201` new, `200` duplicate |
| GET | `/health` | API and DB check |

Errors: `404` account not found, `409` balance must be zero to close, `400` insufficient balance or invalid transfer, `422` request body or `Idempotency-Key` header failed validation.

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
| CONCURRENCY | duplicate key, retries, deadlocks (`transfer_duplicate_key`) |
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
python -m pytest tests/test_race_v0.py -v    # race tests: fail on the v0-naive tag, pass on v1
python -m pytest tests/test_constraints.py -v
python -m pytest tests/test_idempotency.py -v  # 6 tests: crud level and API level
```

To see the v0 bugs yourself, check out the `v0-naive` tag and run the race tests there.

Each test starts from freshly created empty tables (`drop_all` then `create_all`), and `conftest.py` refuses to run if the test database name does not contain `test`.

## Next

v3: request hash, saved response, `409` while a request is in progress (a duplicate that arrives while the first request is still running currently gets the `PENDING` transfer), `422` for the same key with a different body.
