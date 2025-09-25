from pydantic import BaseModel
from typing import Optional
from datetime import datetime


# CANO : 앞 8자리
# ACNT_PRDT_CD : 뒤 2자리
class AccountCreate(BaseModel):
    ACCOUNT_ID: Optional[int] = None
    USER_ID: Optional[str] = None
    ACCOUNT_NO: Optional[str] = None
    AUTH_ID: Optional[int] = None
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None


class AccountResponse(AccountCreate):
    SIMULATION_YN: Optional[str] = None

    model_config = {
        "from_attributes": True,  # SQLAlchemy 모델에서 자동으로 변환
        "populate_by_name": True,  # 필드 이름으로 자동 매핑
        "json_encoders": {
            datetime: lambda dt: dt.isoformat() if dt else None  # datetime 변환 함수 지정
        }
    }