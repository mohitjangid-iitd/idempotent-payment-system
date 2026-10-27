from pydantic import BaseModel, Field, ConfigDict

class AccountCreate(BaseModel):
    name : str = Field(min_length=2, max_length=100)

class AccountOut(BaseModel):
    id : int
    name : str 
    balance : int
    model_config = ConfigDict(from_attributes=True)
