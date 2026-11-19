import asyncio

from sqlalchemy import select, func

from app import crud
from app.models import Transfer


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
        t = await crud.transfer(s, from_id, to_id, amount, idempotency_key=key)
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

    assert errors == []                                # v2: duplicates ko error nahi, wahi transfer
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


async def test_no_header_means_separate_transfers(client, session_factory, make_user):
    a = await make_user("A", 1000)
    b = await make_user("B", 0)
    a_id, b_id = a.id, b.id

    body = {"from_account_id": a_id, "to_account_id": b_id, "amount": 100}

    r1 = await client.post("/transfers", json=body)
    r2 = await client.post("/transfers", json=body)

    assert r1.json()["id"] != r2.json()["id"]
    assert await balance_of(session_factory, a_id) == 800