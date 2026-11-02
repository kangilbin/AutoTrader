"""
Pool 도메인 스키마 - API 요청/응답 DTO
"""
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class PoolSettingUpdateRequest(BaseModel):
    """풀 설정 저장 요청 — 보낸 항목만 바꾼다 (행이 없으면 기본값으로 만든 뒤 반영)

    사용자가 정하는 매수 규칙은 동시 보유 종목 수 하나뿐이다 — 종목당 최대 비중·거래당 리스크는 거기서 자동으로 정해지고
    미청산 리스크 한도는 고정이라 요청에 없다. 보내면 조용히 무시하지 않고 422 로 거절한다 (바뀐 줄 아는 일이 없도록).
    """
    model_config = ConfigDict(extra='forbid')

    ACCOUNT_NO: str
    MARKET: Literal['KR', 'US']
    USE_YN: Optional[Literal['Y', 'N']] = None
    MAX_POSITIONS: Optional[int] = None            # 범위(7~30)는 엔티티가 검증한다
    PARK_RESERVE_PCT: Optional[Decimal] = None


class PoolParkRequest(BaseModel):
    """파킹 실행 요청 — 유휴 현금을 파킹 ETF로 전환"""
    ACCOUNT_NO: str
    MARKET: Literal['KR', 'US']


class PoolSettingResponse(BaseModel):
    """풀 설정 응답"""
    ACCOUNT_NO: str
    MARKET: str
    USE_YN: str
    RISK_PCT: Decimal
    MAX_POSITIONS: Optional[int] = None
    MAX_POSITION_PCT: Decimal
    MAX_OPEN_RISK_PCT: Decimal
    PARK_RESERVE_PCT: Decimal

    model_config = ConfigDict(from_attributes=True)
