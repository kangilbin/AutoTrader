"""
Stock 도메인 엔티티 - 비즈니스 로직 캡슐화
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from decimal import Decimal


@dataclass
class Stock:
    """종목 도메인 엔티티"""
    mrkt_code: str
    st_code: str
    sd_code: str = ""
    name: str = ""
    data_yn: str = "N"
    del_yn: str = "N"
    reg_dt: Optional[datetime] = field(default_factory=datetime.now)
    mod_dt: Optional[datetime] = None

    # ==================== 비즈니스 로직 ====================

    def is_data_loaded(self) -> bool:
        """데이터 적재 완료 여부"""
        return self.data_yn == 'Y'

    def is_delisted(self) -> bool:
        """상장폐지 여부"""
        return self.del_yn == 'Y'

    def mark_data_loaded(self) -> None:
        """데이터 적재 완료 처리"""
        self.data_yn = 'Y'
        self.mod_dt = datetime.now()

    def mark_delisted(self) -> None:
        """상장폐지 처리"""
        self.del_yn = 'Y'
        self.mod_dt = datetime.now()


@dataclass
class StockHistory:
    """주식 일별 데이터 엔티티"""
    mrkt_code: str
    st_code: str
    stck_bsop_date: str  # YYYYMMDD
    stck_oprc: Decimal  # 시가
    stck_hgpr: Decimal  # 고가
    stck_lwpr: Decimal  # 저가
    stck_clpr: Decimal  # 종가
    acml_vol: int  # 거래량
    reg_dt: Optional[datetime] = field(default_factory=datetime.now)
    mod_dt: Optional[datetime] = None

    # ==================== 비즈니스 로직 ====================

    def get_price_change(self) -> Decimal:
        """당일 가격 변동폭"""
        return self.stck_clpr - self.stck_oprc

    def get_price_change_rate(self) -> Decimal:
        """당일 가격 변동률 (%)"""
        if self.stck_oprc == 0:
            return Decimal(0)
        return ((self.stck_clpr - self.stck_oprc) / self.stck_oprc) * 100

    def is_positive(self) -> bool:
        """상승 여부"""
        return self.stck_clpr > self.stck_oprc

    def get_amplitude(self) -> Decimal:
        """당일 진폭 (%)"""
        if self.stck_lwpr == 0:
            return Decimal(0)
        return ((self.stck_hgpr - self.stck_lwpr) / self.stck_lwpr) * 100