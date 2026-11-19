from fastapi import FastAPI, Depends, Request, Header, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import time, uuid
import logging
import structlog

from app.database import get_db
from app.schemas import (
    AccountCreate, AccountOut, AmountIn, TransferIn, 
    AccountNameUpdate, TransferOut
)
from app.crud import (
    create_user, get_user, update_user_name, transfer, 
    get_all_users, delete_user, debit_user, credit_user
)
from app.exceptions import (
    AccountNotFound, InsufficientBalance, BalanceNotZero, 
    InvalidTransfer, RequestBodyChangeError, RequestInProgress
    )
from app.logging_config import setup_logging

app = FastAPI()

# --- middlewares ---
setup_logging()
log = structlog.get_logger()

@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = str(uuid.uuid4())
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("request_failed", log_type="ERROR",
                      method=request.method, path=request.url.path)
        raise
    log.info("request", log_type="REQUEST", method=request.method,
             path=request.url.path, status=response.status_code,
             duration_ms=round((time.perf_counter() - start) * 1000, 1))
    response.headers["X-Request-ID"] = request_id
    return response

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

@app.exception_handler(RequestBodyChangeError)
async def request_body_change_handler(request: Request, exc: RequestBodyChangeError):
    return JSONResponse(
        status_code=422,
        content={"detail": "Idempotency-Key was already used with a different request body"}
    )

@app.exception_handler(RequestInProgress)
async def request_in_progress_handler(request: Request, exc: RequestInProgress):
    return JSONResponse(
        status_code=409,
        content={"detail": "A request with this Idempotency-Key is still in progress, retry shortly"},
        headers={"Retry-After": "1"},
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

@app.get("/accounts", response_model=list[AccountOut])
async def get_all_accounts(session: AsyncSession = Depends(get_db)):
    return await get_all_users(session)

@app.patch("/accounts/{account_id}", response_model=AccountOut)
async def update_account(account_id: int, body: AccountNameUpdate, session: AsyncSession = Depends(get_db)):
    return await update_user_name(session, account_id, body.name)

@app.delete("/accounts/{account_id}", response_model=AccountOut)
async def delete_account(account_id: int, session: AsyncSession = Depends(get_db)):
    return await delete_user(session, account_id)

@app.post("/accounts/{account_id}/deposit", response_model=AccountOut)
async def deposit(account_id: int, body: AmountIn, session: AsyncSession = Depends(get_db)):
    user = await credit_user(session, account_id, body.amount)
    log.info("deposit_succeeded", log_type="AUDIT",
             user_id=account_id, amount=body.amount)
    return user

@app.post("/accounts/{account_id}/withdraw", response_model=AccountOut)
async def withdraw(account_id: int, body: AmountIn, session: AsyncSession = Depends(get_db)):
    user = await debit_user(session, account_id, body.amount)
    log.info("withdraw_succeeded", log_type="AUDIT",
             user_id=account_id, amount=body.amount)
    return user

@app.post("/transfers", response_model=TransferOut, status_code=201)
async def create_transfer(response:Response, body: TransferIn, session: AsyncSession = Depends(get_db), idempotency_key: str = Header(min_length=1, max_length=64)):
    result, created = await transfer(session, body.from_account_id, body.to_account_id, body.amount, idempotency_key=idempotency_key)
    if not created:
        response.status_code = 200
    return result

