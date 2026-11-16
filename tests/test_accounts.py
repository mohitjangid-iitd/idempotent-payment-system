import pytest
from sqlalchemy import select, func

from app import crud
from app.models import Transfer
from app.exceptions import (
    AccountNotFound,
    InsufficientBalance,
    BalanceNotZero,
    InvalidTransfer,
)


async def count_transfers(session) -> int:
    result = await session.execute(select(func.count()).select_from(Transfer))
    return result.scalar_one()


# ---------- accounts ----------

async def test_create_account(session):
    user = await crud.create_user(session, "Mohit")
    assert user.id is not None
    assert user.name == "Mohit"
    assert user.balance == 0
    assert user.deleted_at is None


async def test_get_account(session, make_user):
    a = await make_user("A")
    found = await crud.get_user(session, a.id)
    assert found is not None
    assert found.name == "A"


async def test_get_missing_account_returns_none(session):
    assert await crud.get_user(session, 9999) is None


async def test_get_all_users(session, make_user):
    await make_user("A")
    await make_user("B")
    users = await crud.get_all_users(session)
    assert len(users) == 2


async def test_update_name(session, make_user):
    a = await make_user("Old")
    updated = await crud.update_user_name(session, a.id, "New")
    assert updated.name == "New"


async def test_update_name_missing_account(session):
    with pytest.raises(AccountNotFound):
        await crud.update_user_name(session, 9999, "X")


# ---------- delete (soft delete) ----------

async def test_delete_empty_account(session, make_user):
    a = await make_user("A")
    deleted = await crud.delete_user(session, a.id)
    assert deleted.deleted_at is not None
    # soft delete ke baad ab wo "active" nahi dikhna chahiye
    assert await crud.get_user(session, a.id) is None
    assert await crud.get_all_users(session) == []


async def test_delete_with_balance_rejected(session, make_user):
    a = await make_user("A", 100)
    with pytest.raises(BalanceNotZero):
        await crud.delete_user(session, a.id)
    assert await crud.get_user(session, a.id) is not None


async def test_delete_twice_is_not_found(session, make_user):
    a = await make_user("A")
    await crud.delete_user(session, a.id)
    with pytest.raises(AccountNotFound):
        await crud.delete_user(session, a.id)


# ---------- deposit / withdraw ----------

async def test_deposit_updates_balance(session, make_user):
    a = await make_user("A", 100)
    user = await crud.credit_user(session, a.id, 50)
    assert user.balance == 150


async def test_withdraw_updates_balance(session, make_user):
    a = await make_user("A", 100)
    user = await crud.debit_user(session, a.id, 30)
    assert user.balance == 70


async def test_withdraw_more_than_balance_rejected(session, make_user):
    a = await make_user("A", 100)
    with pytest.raises(InsufficientBalance):
        await crud.debit_user(session, a.id, 101)
    user = await crud.get_user(session, a.id)
    assert user.balance == 100


@pytest.mark.parametrize("bad_amount", [0, -1, -500])
async def test_deposit_invalid_amount_rejected(session, make_user, bad_amount):
    a = await make_user("A", 100)
    with pytest.raises(InvalidTransfer):
        await crud.credit_user(session, a.id, bad_amount)


@pytest.mark.parametrize("bad_amount", [0, -1, -500])
async def test_withdraw_invalid_amount_rejected(session, make_user, bad_amount):
    a = await make_user("A", 100)
    with pytest.raises(InvalidTransfer):
        await crud.debit_user(session, a.id, bad_amount)
    user = await crud.get_user(session, a.id)
    assert user.balance == 100


async def test_deposit_missing_account(session):
    with pytest.raises(AccountNotFound):
        await crud.credit_user(session, 9999, 10)


# ---------- transfer ----------

async def test_transfer_success(session, make_user):
    a = await make_user("A", 500)
    b = await make_user("B", 100)

    t = await crud.transfer(session, a.id, b.id, 200)

    await session.refresh(a)
    await session.refresh(b)
    assert t.status == "SUCCESS"
    assert a.balance == 300
    assert b.balance == 300
    # paisa na paida hua na gayab
    assert a.balance + b.balance == 600


async def test_transfer_insufficient_balance(session, make_user):
    a = await make_user("A", 100)
    b = await make_user("B", 0)

    with pytest.raises(InsufficientBalance):
        await crud.transfer(session, a.id, b.id, 500)

    await session.refresh(a)
    await session.refresh(b)
    assert a.balance == 100
    assert b.balance == 0

    # v0 design: row ban chuki thi, status FAILED
    result = await session.execute(select(Transfer))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "FAILED"


async def test_transfer_self_rejected_no_row(session, make_user):
    a = await make_user("A", 100)
    with pytest.raises(InvalidTransfer):
        await crud.transfer(session, a.id, a.id, 10)
    assert await count_transfers(session) == 0


@pytest.mark.parametrize("bad_amount", [0, -1, -500])
async def test_transfer_invalid_amount_rejected_no_row(session, make_user, bad_amount):
    a = await make_user("A", 100)
    b = await make_user("B", 0)
    with pytest.raises(InvalidTransfer):
        await crud.transfer(session, a.id, b.id, bad_amount)
    assert await count_transfers(session) == 0


async def test_transfer_missing_receiver_no_row(session, make_user):
    a = await make_user("A", 100)
    with pytest.raises(AccountNotFound):
        await crud.transfer(session, a.id, 9999, 10)
    assert await count_transfers(session) == 0


async def test_transfer_missing_sender_no_row(session, make_user):
    b = await make_user("B", 0)
    with pytest.raises(AccountNotFound):
        await crud.transfer(session, 9999, b.id, 10)
    assert await count_transfers(session) == 0


async def test_transfer_to_deleted_account_rejected(session, make_user):
    a = await make_user("A", 100)
    b = await make_user("B", 0)
    await crud.delete_user(session, b.id)
    with pytest.raises(AccountNotFound):
        await crud.transfer(session, a.id, b.id, 10)
    assert await count_transfers(session) == 0
