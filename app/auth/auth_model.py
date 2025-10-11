from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class AuthCreate(BaseModel):
    AUTH_ID: Optional[int] = None
    AUTH_NAME: Optional[str] = None
    USER_ID: Optional[str] = None
    SIMULATION_YN: Optional[str] = None
    API_KEY: Optional[str] = None
    SECRET_KEY: Optional[str] = None
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None

class AuthChoice(BaseModel):
    AUTH_ID: Optional[int] = None
    ACCOUNT_NO: Optional[int] = None

class AuthResponse(AuthCreate):
    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "exclude": {"API_KEY", "SECRET_KEY"}
        }
    }
