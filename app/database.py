from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=50,
    max_overflow=0,
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
)

async def get_db():
    async with SessionLocal() as session:
        yield session

