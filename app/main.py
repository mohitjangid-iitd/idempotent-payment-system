from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import AccountCreate, AccountOut
from app.crud import (
    create_user, get_user
    )

app = FastAPI()

@app.get("/")
def read_root():
    return {"Hello": "World"}

@app.get("/health")
async def health(session: AsyncSession = Depends(get_db)):
    await session.execute(text("select 1"))
    return {"status": "ok"}

@app.post("/accounts", response_model=AccountOut, status_code=201)
async def create_account(body: AccountCreate, session: AsyncSession = Depends(get_db)):
    return await create_user(session, body.name)

@app.get("/accounts/{account_id}", response_model=AccountOut)
async def get_account(account_id: int, session: AsyncSession = Depends(get_db)):
    user = await get_user(session, account_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return user