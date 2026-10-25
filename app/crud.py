from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import User
from app.exceptions import AccountNotFound, InsufficientBalance, BalanceNotZero

async def get_user(session: AsyncSession, user_id: int) -> Optional[User]:
    stmt = select(User).where(User.id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def get_user_by_name(session: AsyncSession, user_name: str) -> List[User]:
    stmt = select(User).where(User.name == user_name)
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_all_users(session: AsyncSession) -> List[User]:
    stmt = select(User)
    result = await session.execute(stmt)
    return result.scalars().all()

async def create_user(session: AsyncSession, name: str) -> User:
    user = User(name=name)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user

async def update_user_name(session: AsyncSession, user_id: int, name: str) -> Optional[User]:
    user = await get_user(session, user_id)
    if user:
        user.name = name
        await session.commit()
        await session.refresh(user)
        return user
    raise AccountNotFound()

async def delete_user(session: AsyncSession, user_id: int) -> User:
    user = await get_user(session, user_id)
    if user:
        if user.balance != 0:
            raise BalanceNotZero()
        else:
            await session.delete(user)
            await session.commit()
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
