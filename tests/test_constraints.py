import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


async def test_db_rejects_negative_balance(session, make_user):
    a = await make_user("A", 100)
    a_id = a.id

    # app code ko bypass karke seedha SQL
    with pytest.raises(IntegrityError):
        await session.execute(
            text("UPDATE user_table SET balance = -1 WHERE id = :id"),
            {"id": a_id},
        )
        await session.commit()