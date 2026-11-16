from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update

import structlog

from app.models import User, Transfer
from app.exceptions import AccountNotFound, InsufficientBalance, BalanceNotZero, InvalidTransfer

log = structlog.get_logger()


def _active(stmt):
    return stmt.where(User.deleted_at.is_(None))

async def get_user(session: AsyncSession, user_id: int) -> Optional[User]:
    stmt = _active(select(User).where(User.id == user_id))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def get_user_for_update(session: AsyncSession, user_id: int) -> Optional[User]:
    stmt = _active(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def get_user_by_name(session: AsyncSession, user_name: str) -> List[User]:
    stmt = _active(select(User).where(User.name == user_name))
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_all_users(session: AsyncSession) -> List[User]:
    stmt = _active(select(User))
    result = await session.execute(stmt)
    return result.scalars().all()

async def create_user(session: AsyncSession, name: str) -> User:
    user = User(name=name)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    log.info("account_created", log_type="BUSINESS", user_id=user.id)
    return user

async def update_user_name(session: AsyncSession, user_id: int, name: str) -> User:
    user = await get_user(session, user_id)
    if user is None:
        log.warning("account_update_rejected", log_type="REJECT",
                    reason="account_not_found", user_id=user_id)
        raise AccountNotFound()

    user.name = name
    await session.commit()
    await session.refresh(user)
    log.info("account_renamed", log_type="BUSINESS", user_id=user.id)
    return user

async def delete_user(session: AsyncSession, user_id: int) -> User:
    user = await get_user(session, user_id)
    if user is None:
        log.warning("account_delete_rejected", log_type="REJECT",
                    reason="account_not_found", user_id=user_id)
        raise AccountNotFound()

    if user.balance != 0:
        log.warning("account_delete_rejected", log_type="REJECT",
                    reason="balance_not_zero", user_id=user_id,
                    balance_read=user.balance)
        raise BalanceNotZero()

    user.deleted_at = func.now()
    await session.commit()
    await session.refresh(user)
    log.info("account_deleted", log_type="BUSINESS", user_id=user.id)
    return user

async def _credit(session: AsyncSession, user_id: int, amount: int) -> User:
    if amount <= 0:
        log.warning("credit_rejected", log_type="REJECT",
                    reason="invalid_amount", user_id=user_id, amount=amount)
        raise InvalidTransfer()

    user = await get_user_for_update(session, user_id)
    if user is None:
        log.warning("credit_rejected", log_type="REJECT",
                    reason="account_not_found", user_id=user_id)
        raise AccountNotFound()

    balance_read = user.balance
    user.balance += amount
    log.debug("balance_changed", log_type="DEBUG-EVIDENCE", op="credit",
              user_id=user_id, amount=amount,
              balance_read=balance_read, balance_written=user.balance)
    return user

async def credit_user(session: AsyncSession, user_id: int, amount: int) -> User:
    user = await _credit(session, user_id, amount)
    await session.commit()
    await session.refresh(user)
    return user

async def _debit(session: AsyncSession, user_id: int, amount: int) -> User:
    if amount <= 0:
        log.warning("debit_rejected", log_type="REJECT",
                    reason="invalid_amount", user_id=user_id, amount=amount)
        raise InvalidTransfer()

    user = await get_user_for_update(session, user_id)
    if user is None:
        log.warning("debit_rejected", log_type="REJECT",
                    reason="account_not_found", user_id=user_id)
        raise AccountNotFound()

    if user.balance < amount:
        log.warning("debit_rejected", log_type="REJECT",
                    reason="insufficient_balance", user_id=user_id,
                    amount=amount, balance_read=user.balance)
        raise InsufficientBalance()

    balance_read = user.balance
    user.balance -= amount
    log.debug("balance_changed", log_type="DEBUG-EVIDENCE", op="debit",
              user_id=user_id, amount=amount,
              balance_read=balance_read, balance_written=user.balance)
    return user

async def debit_user(session: AsyncSession, user_id: int, amount: int) -> User:
    user = await _debit(session, user_id, amount)    
    await session.commit()
    await session.refresh(user)
    return user

async def transfer(session: AsyncSession, from_user_id: int, to_user_id: int, amount: int) -> Transfer:
    if from_user_id == to_user_id:
        log.warning("transfer_rejected", log_type="REJECT",
                    reason="self_transfer", sender_id=from_user_id)
        raise InvalidTransfer()

    if amount <= 0:
        log.warning("transfer_rejected", log_type="REJECT",
                    reason="invalid_amount", sender_id=from_user_id, amount=amount)
        raise InvalidTransfer()

    if await get_user_for_update(session, from_user_id) is None:
        log.warning("transfer_rejected", log_type="REJECT",
                    reason="sender_not_found", sender_id=from_user_id)
        raise AccountNotFound()

    if await get_user_for_update(session, to_user_id) is None:
        log.warning("transfer_rejected", log_type="REJECT",
                    reason="receiver_not_found", receiver_id=to_user_id)
        raise AccountNotFound()

    transfer = Transfer(
        sender_id=from_user_id,
        receiver_id=to_user_id,
        amount=amount,
        status="PENDING",
    )
    session.add(transfer)
    await session.commit()
    await session.refresh(transfer)
    transfer_id = transfer.id
    structlog.contextvars.bind_contextvars(transfer_id=transfer_id)
    log.info("transfer_created", log_type="BUSINESS",
             sender_id=from_user_id, receiver_id=to_user_id, amount=amount)

    try:
        
        await _debit(session, from_user_id, amount)
        await _credit(session, to_user_id, amount)
        transfer.status = "SUCCESS"
        await session.commit()
    except Exception as exc:
        await session.rollback()
        await session.execute(
            update(Transfer).where(Transfer.id == transfer_id).values(status="FAILED")
        )
        await session.commit()
        log.warning("transfer_failed", log_type="BUSINESS",
                    reason=type(exc).__name__,
                    sender_id=from_user_id, receiver_id=to_user_id, amount=amount)
        raise

    await session.refresh(transfer)
    log.info("transfer_succeeded", log_type="AUDIT",
             sender_id=from_user_id, receiver_id=to_user_id, amount=amount)
    return transfer