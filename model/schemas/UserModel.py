from pydantic import BaseModel
from typing import Optional


class UserCreate(BaseModel):
    USER_ID: str
    USER_NAME: Optional[str] = None
    PASSWORD: str
    DEVICE_ID: str
    REG_DT: Optional[str] = None
    MOD_DT: Optional[str] = None

class UserResponse(UserCreate):

    class Config:
        orm_mode = True