from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import AccountCreate, AccountOut
from app.crud import (
    create_user, get_user
    )
from app.exceptions import (
    AccountNotFound, 
    InsufficientBalance, 
    BalanceNotZero, 
    InvalidTransfer
    )

app = FastAPI()

# --- global exception handlers ---
@app.exception_handler(AccountNotFound)
async def account_not_found_handler(request: Request, exc: AccountNotFound):
    return JSONResponse(
        status_code=404,
        content={"detail": "Account not found"}
    )

@app.exception_handler(InsufficientBalance)
async def insufficient_balance_handler(request: Request, exc: InsufficientBalance):
    return JSONResponse(
        status_code=400,
        content={"detail": "Insufficient balance"}
    )

@app.exception_handler(BalanceNotZero)
async def balance_not_zero_handler(request: Request, exc: BalanceNotZero):
    return JSONResponse(
        status_code=409,
        content={"detail": "Account balance must be zero to close"}
    )

@app.exception_handler(InvalidTransfer)
async def invalid_transfer_handler(request: Request, exc: InvalidTransfer):
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid transfer request"}
    )


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
        raise AccountNotFound()
    return user