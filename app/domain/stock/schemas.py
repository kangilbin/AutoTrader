"""
Stock DTO (Data Transfer Object)
"""
from datetime import datetime
from pydantic import BaseModel
from typing import Optional
from decimal import Decimal


class StockResponse(BaseModel):
    """종목 정보 응답"""
    MRKT_CODE: str
    ST_CODE: str
    SD_CODE: Optional[str] = None
    ST_NM: Optional[str] = None
    DATA_YN: Optional[str] = None
    DEL_YN: Optional[str] = None
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None

    model_config = {
        "from_attributes": True,
        "json_encoders": {
            datetime: lambda dt: dt.isoformat() if dt else None
        }
    }


class StockHistoryResponse(BaseModel):
    """주식 일별 데이터 응답"""
    MRKT_CODE: str
    ST_CODE: str
    STCK_BSOP_DATE: str
    STCK_OPRC: Decimal
    STCK_HGPR: Decimal
    STCK_LWPR: Decimal
    STCK_CLPR: Decimal
    ACML_VOL: int
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None

    model_config = {
        "from_attributes": True,
        "json_encoders": {
            datetime: lambda dt: dt.isoformat() if dt else None
        }
    }


class StockPriceResponse(BaseModel):
    """호가 조회 응답"""
    askp1: Optional[str] = None  # 매도호가1
    askp2: Optional[str] = None
    bidp1: Optional[str] = None  # 매수호가1
    bidp2: Optional[str] = None
    # 추가 필드는 KIS API 응답에 따라 확장