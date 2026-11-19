import asyncio

from sqlalchemy import select, func

from app import crud
from app.models import Transfer
from app.exceptions import RequestInProgress


async def count_transfers(session_factory) -> int:
    async with session_factory() as s:
        result = await s.execute(select(func.count()).select_from(Transfer))
        return result.scalar_one()


async def balance_of(session_factory, user_id) -> int:
    async with session_factory() as s:
        user = await crud.get_user(s, user_id)
        return user.balance


async def key_worker(session_factory, from_id, to_id, amount, key) -> int:
    # har call = ek alag "request", apna session
    async with session_factory() as s:
        t, _ = await crud.transfer(s, from_id, to_id, amount, idempotency_key=key)
        return t.id


# ---------- retry: pehli request khatam ho chuki, client ne dobara bheji ----------

async def test_retry_same_key_moves_money_once(session_factory, make_user):
    a = await make_user("A", 1000)
    b = await make_user("B", 0)
    a_id, b_id = a.id, b.id

    first_id = await key_worker(session_factory, a_id, b_id, 100, "key-1")
    second_id = await key_worker(session_factory, a_id, b_id, 100, "key-1")

    assert await count_transfers(session_factory) == 1
    assert second_id == first_id                       # v2: wahi purani transfer wapas
    assert await balance_of(session_factory, a_id) == 900
    assert await balance_of(session_factory, b_id) == 100


# ---------- guard: alag keys = alag transfers (over-dedupe na ho) ----------

async def test_different_keys_are_different_transfers(session_factory, make_user):
    a = await make_user("A", 1000)
    b = await make_user("B", 0)
    a_id, b_id = a.id, b.id

    id1 = await key_worker(session_factory, a_id, b_id, 100, "key-1")
    id2 = await key_worker(session_factory, a_id, b_id, 100, "key-2")

    assert id1 != id2
    assert await count_transfers(session_factory) == 2
    assert await balance_of(session_factory, a_id) == 800
    assert await balance_of(session_factory, b_id) == 200


# ---------- double click: same key, 20 requests ek saath ----------

async def test_double_click_same_key_moves_money_once(session_factory, make_user):
    # A ke paas 5000: bug hone par bhi sab 20 chalein, to 2000 kat ke dikhe
    a = await make_user("A", 5000)
    b = await make_user("B", 0)
    a_id, b_id = a.id, b.id

    results = await asyncio.gather(
        *[key_worker(session_factory, a_id, b_id, 100, "key-dc") for _ in range(20)],
        return_exceptions=True,
    )

    errors = [r for r in results if isinstance(r, Exception)]
    ids = {r for r in results if not isinstance(r, Exception)}

    assert all(isinstance(e, RequestInProgress) for e in errors)
    assert len(ids) == 1                               # sabko ek hi transfer ka id
    assert await count_transfers(session_factory) == 1
    assert await balance_of(session_factory, a_id) == 4900
    assert await balance_of(session_factory, b_id) == 100


# ---------- API level: Idempotency-Key header ----------

async def test_header_same_key_moves_money_once(client, session_factory, make_user):
    a = await make_user("A", 1000)
    b = await make_user("B", 0)
    a_id, b_id = a.id, b.id

    body = {"from_account_id": a_id, "to_account_id": b_id, "amount": 100}
    headers = {"Idempotency-Key": "hdr-1"}

    r1 = await client.post("/transfers", json=body, headers=headers)
    r2 = await client.post("/transfers", json=body, headers=headers)

    assert r1.json()["id"] == r2.json()["id"]
    assert await count_transfers(session_factory) == 1
    assert await balance_of(session_factory, a_id) == 900


async def test_missing_header_is_rejected(client, session_factory, make_user):
    a = await make_user("A", 1000)
    b = await make_user("B", 0)
    a_id, b_id = a.id, b.id

    body = {"from_account_id": a_id, "to_account_id": b_id, "amount": 100}
    r = await client.post("/transfers", json=body)

    assert r.status_code == 422
    assert await count_transfers(session_factory) == 0
    assert await balance_of(session_factory, a_id) == 1000


async def test_different_header_keys_are_separate_transfers(client, session_factory, make_user):
    a = await make_user("A", 1000)
    b = await make_user("B", 0)
    a_id, b_id = a.id, b.id

    body = {"from_account_id": a_id, "to_account_id": b_id, "amount": 100}
    r1 = await client.post("/transfers", json=body, headers={"Idempotency-Key": "k1"})
    r2 = await client.post("/transfers", json=body, headers={"Idempotency-Key": "k2"})

    assert r1.json()["id"] != r2.json()["id"]
    assert await balance_of(session_factory, a_id) == 800

async def test_same_key_different_body_is_rejected(client, session_factory, make_user):
    a = await make_user("A", 1000)
    b = await make_user("B", 0)
    a_id, b_id = a.id, b.id

    headers = {"Idempotency-Key": "mismatch-1"}
    first = {"from_account_id": a_id, "to_account_id": b_id, "amount": 100}
    second = {"from_account_id": a_id, "to_account_id": b_id, "amount": 500}

    r1 = await client.post("/transfers", json=first, headers=headers)
    r2 = await client.post("/transfers", json=second, headers=headers)

    assert r1.status_code == 201
    assert r2.status_code == 422
    assert await count_transfers(session_factory) == 1
    assert await balance_of(session_factory, a_id) == 900 
    assert await balance_of(session_factory, b_id) == 100

async def test_duplicate_while_first_in_progress_returns_409(client, session_factory, make_user):
    a = await make_user("A", 1000)
    b = await make_user("B", 0)
    a_id, b_id = a.id, b.id

    # simulate a first request that is still running: PENDING row, no money moved yet
    async with session_factory() as s:
        s.add(Transfer(sender_id=a_id, receiver_id=b_id, amount=100,
                       status="PENDING", idempotency_key="in-progress-1"))
        await s.commit()

    body = {"from_account_id": a_id, "to_account_id": b_id, "amount": 100}
    r = await client.post("/transfers", json=body, headers={"Idempotency-Key": "in-progress-1"})

    assert r.status_code == 409
    assert await count_transfers(session_factory) == 1
    assert await balance_of(session_factory, a_id) == 1000   # nothing moved

async def test_retry_of_failed_transfer_is_rejected(client, session_factory, make_user):
    a = await make_user("A", 1000)
    b = await make_user("B", 0)
    a_id, b_id = a.id, b.id

    # simulate an earlier attempt that failed: FAILED row with the key, no money moved
    async with session_factory() as s:
        s.add(Transfer(sender_id=a_id, receiver_id=b_id, amount=100,
                       status="FAILED", idempotency_key="failed-1"))
        await s.commit()

    body = {"from_account_id": a_id, "to_account_id": b_id, "amount": 100}
    r = await client.post("/transfers", json=body, headers={"Idempotency-Key": "failed-1"})

    assert r.status_code == 409
    assert "Retry-After" not in r.headers          # do not retry with the same key
    assert await count_transfers(session_factory) == 1
    assert await balance_of(session_factory, a_id) == 1000   # nothing moved