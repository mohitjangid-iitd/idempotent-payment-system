from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import BigInteger, String, ForeignKey, DateTime, func, CheckConstraint
from datetime import datetime

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "user_table"

    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_user_balance_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    balance: Mapped[int] = mapped_column(BigInteger, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("user_table.id"))
    receiver_id: Mapped[int] = mapped_column(ForeignKey("user_table.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )