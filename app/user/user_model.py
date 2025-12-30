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
        data = self.model_dump()
        data["REG_DT"] = self.REG_DT.isoformat() if self.REG_DT else None
        data["MOD_DT"] = self.MOD_DT.isoformat() if isinstance(self.MOD_DT, datetime) else ""
        return data
    
    model_config = {
        "from_attributes": True,  # SQLAlchemy 모델에서 자동으로 변환
        "populate_by_name": True,  # 필드 이름으로 자동 매핑
        "json_encoders": {
            datetime: lambda dt: dt.isoformat() if dt else None  # datetime 변환 함수 지정
        }
    }