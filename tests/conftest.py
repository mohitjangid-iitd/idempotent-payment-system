import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app import crud
from app.config import settings
from app.database import get_db
from app.main import app
from app.models import Base

assert "test" in settings.test_db_name


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine(
        settings.test_database_url,
        pool_size=60,
        max_overflow=0,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest_asyncio.fixture
async def make_user(session):
    async def _make(name: str = "user", balance: int = 0):
        user = await crud.create_user(session, name)
        if balance:
            user = await crud.credit_user(session, user.id, balance)
        return user

    return _make


@pytest_asyncio.fixture
async def client(session_factory):
    # app ka get_db dev DB se judta hai, test mein usse test DB wala session do
    async def override_get_db():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()