import asyncio
import pytest
from app import crud
from app.exceptions import InsufficientBalance
from sqlalchemy import select
from app.models import Transfer

async def credit_worker(session_factory, user_id, amount):
    # har task apna session kholta hai
    async with session_factory() as s:
        await crud.credit_user(s, user_id, amount)


async def test_lost_update(session_factory, make_user):
    a = await make_user("A", 0)

    # 50 tasks ek saath
    await asyncio.gather(
        *[credit_worker(session_factory, a.id, 100) for _ in range(50)]
    )

    # naye session se asli balance padho
    async with session_factory() as s:
        final = await crud.get_user(s, a.id)

    assert final.balance == 5000

async def withdraw_worker(session_factory, user_id, amount):
    async with session_factory() as s:
        return await crud.debit_user(s, user_id, amount)


async def test_negative_balance(session_factory, make_user):
    a = await make_user("A", 100)

    # 10 withdrawals ek saath, har ek 100 ka
    results = await asyncio.gather(
        *[withdraw_worker(session_factory, a.id, 100) for _ in range(10)],
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    rejected = [r for r in results if isinstance(r, InsufficientBalance)]

    async with session_factory() as s:
        final = await crud.get_user(s, a.id)

    assert len(successes) == 1      # sirf ek ko paisa milna chahiye
    assert len(rejected) == 9       # baaki 9 reject
    assert final.balance == 0

async def test_crash_midway(session, session_factory, make_user, monkeypatch):
    a = await make_user("A", 500)
    b = await make_user("B", 0)
    a_id, b_id = a.id, b.id

    async def failing_credit(session, user_id, amount):
        raise RuntimeError("crash between debit and credit")

    monkeypatch.setattr(crud, "_credit", failing_credit)    # credit_user nahi, _credit

    with pytest.raises(RuntimeError):
        await crud.transfer(session, a_id, b_id, 200)

    # naye session se asli DB state
    async with session_factory() as s:
        a_after = await crud.get_user(s, a_id)
        b_after = await crud.get_user(s, b_id)
        rows = (await s.execute(select(Transfer))).scalars().all()

    # sahi behaviour: kuch bhi aadha nahi, aur failure ka record
    assert a_after.balance == 500
    assert b_after.balance == 0
    assert len(rows) == 1
    assert rows[0].status == "FAILED"