from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class SwingCreate(BaseModel):
    ST_CODE: Optional[str] = None
    USER_ID: Optional[str] = None
    ACCOUNT_NO: Optional[str] = None
    USE_YN: Optional[str] = None
    SWING_AMOUNT: Optional[int] = None
    SWING_TYPE: Optional[str] = None
    SHORT_TERM: Optional[int] = None
    MEDIUM_TERM: Optional[int] = None
    LONG_TERM: Optional[int] = None
    BUY_RATIO: Optional[int] = None
    SELL_RATIO: Optional[int] = None
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None

class SwingResponse(SwingCreate):
     model_config = {
        "from_attributes": True,  # SQLAlchemy 모델에서 자동으로 변환
        "populate_by_name": True,  # 필드 이름으로 자동 매핑
        "json_encoders": {
            datetime: lambda dt: dt.isoformat() if dt else None  # datetime 변환 함수 지정
        }
    }