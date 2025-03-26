from pydantic import BaseModel
from typing import Optional

class AuthCreate(BaseModel):
    AUTH_ID: int
    USER_ID: Optional[str] = None
    SIMULATION_YN: Optional[str] = None
    API_KEY: Optional[str] = None
    SECRET_KEY: Optional[str] = None
    REG_DT: Optional[str] = None
    MOD_DT: Optional[str] = None

class AuthResponse(AuthCreate):

    class Config:
        orm_mode = True
