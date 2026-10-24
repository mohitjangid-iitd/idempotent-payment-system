from fastapi import FastAPI, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

app = FastAPI()

@app.get("/")
def read_root():
    return {"Hello": "World"}

@app.get("/health")
async def health(session: AsyncSession = Depends(get_db)):
    await session.execute(text("select 1"))
    return {"status": "ok"}