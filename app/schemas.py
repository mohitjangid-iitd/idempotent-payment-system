from pydantic import BaseModel, Field, ConfigDict

class AccountCreate(BaseModel):
    name : str = Field(min_length=2, max_length=100)

class AccountOut(BaseModel):
    id : int
    name : str 
    balance : int
    model_config = ConfigDict(from_attributes=True)

class AmountIn(BaseModel):
    amount: int = Field(gt=0)

class TransferIn(BaseModel):
    from_account_id: int
    to_account_id: int
    amount: int = Field(gt=0)

class TransferOut(BaseModel):
    id: int
    sender_id: int
    receiver_id: int
    amount: int
    status: str
    model_config = ConfigDict(from_attributes=True)

class AccountNameUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
