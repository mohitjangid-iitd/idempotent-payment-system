from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import User, Transfer
from app.exceptions import AccountNotFound, InsufficientBalance, BalanceNotZero, InvalidTransfer

def _active(stmt):
    return stmt.where(User.deleted_at.is_(None))

async def get_user(session: AsyncSession, user_id: int) -> Optional[User]:
    stmt = _active(select(User).where(User.id == user_id))
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
    return user

async def update_user_name(session: AsyncSession, user_id: int, name: str) -> User:
    user = await get_user(session, user_id)
    if user is None:
        raise AccountNotFound()
    
    user.name = name
    await session.commit()
    await session.refresh(user)
    return user

async def delete_user(session: AsyncSession, user_id: int) -> User:
    user = await get_user(session, user_id)
    if user:
        if user.balance != 0:
            raise BalanceNotZero()
        else:
            user.deleted_at = func.now()
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user
    raise AccountNotFound()

async def credit_user(session: AsyncSession, user_id: int, amount: int) -> User:
    if amount <= 0:
        raise ValueError("Amount must be positive")
    user = await get_user(session, user_id)
    if user:
        user.balance += amount
        await session.commit()
        await session.refresh(user)
        return user
    raise AccountNotFound()

async def debit_user(session: AsyncSession, user_id: int, amount: int) -> User:
    if amount <= 0:
        raise ValueError("Amount must be positive")
    user = await get_user(session, user_id)
    if user:
        if user.balance < amount:
            raise InsufficientBalance()
        user.balance -= amount
        await session.commit()
        await session.refresh(user)
        return user
    raise AccountNotFound()

async def transfer(session: AsyncSession, from_user_id: int, to_user_id: int, amount: int) -> Transfer:
    if from_user_id == to_user_id:
        raise InvalidTransfer()
    
    if amount <= 0:
        raise ValueError("Amount must be positive")

    from_user = await get_user(session, from_user_id)
    if from_user is None:
        raise AccountNotFound()

    to_user = await get_user(session, to_user_id)
    if to_user is None:
        raise AccountNotFound()
    
    transfer = Transfer(
        sender_id=from_user_id,
        receiver_id=to_user_id,
        amount=amount,
        status="PENDING"
    )
    session.add(transfer)
    await session.commit()
    await session.refresh(transfer)
    
    if from_user.balance < amount:
        transfer.status = "FAILED"
        await session.commit()
        raise InsufficientBalance()
    
    from_user = await debit_user(session, from_user_id, amount)
    to_user = await credit_user(session, to_user_id, amount)

    transfer.status = "SUCCESS"

    await session.commit()
    await session.refresh(transfer)
    return transfer

