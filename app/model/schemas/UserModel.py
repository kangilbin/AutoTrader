from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class UserCreate(BaseModel):
    USER_ID: str
    USER_NAME: Optional[str] = None
    PHONE: Optional[str] = None
    PASSWORD: Optional[str] = None
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None


class UserResponse(UserCreate):
    def to_dict(self):
        data = self.dict()
        data["REG_DT"] = self.REG_DT.isoformat() if self.REG_DT else None
        data["MOD_DT"] = self.MOD_DT.isoformat() if isinstance(self.MOD_DT, datetime) else ""
        return data

    class Config:
        model_config = {"from_attributes": True} 