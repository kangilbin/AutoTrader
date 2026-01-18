"""
실시간 거래 전략의 추상 베이스 클래스
"""
from abc import ABC, abstractmethod
from typing import Dict, Optional
import pandas as pd
from decimal import Decimal


class TradingStrategy(ABC):
    """
    실시간 거래 전략 베이스 클래스

    모든 실시간 거래 전략은 이 클래스를 상속하고
    check_entry_signal, check_exit_signal, check_second_buy_signal을 구현해야 합니다.

    Attributes:
        name: 전략 이름 (클래스 속성으로 정의)
    """

    name: str = "기본 전략"

    @classmethod
    @abstractmethod
    async def check_entry_signal(
        cls,
        redis_client,
        symbol: str,
        df: pd.DataFrame,
        current_price: Decimal,
        frgn_ntby_qty: int,
        pgtr_ntby_qty: int,
        acml_vol: int,
        prdy_vrss_vol_rate: float,
        prdy_ctrt: float
    ) -> Optional[Dict]:
        """
        1차 매수 진입 신호 체크 (하위 클래스에서 구현 필수)

        Args:
            redis_client: Redis 클라이언트
            symbol: 종목코드
            df: 주가 데이터
            current_price: 현재가
            frgn_ntby_qty: 외국인 순매수량
            pgtr_ntby_qty: 프로그램 순매수량
            acml_vol: 누적거래량
            prdy_vrss_vol_rate: 전일대비 거래량 비율
            prdy_ctrt: 전일대비 상승률

        Returns:
            매수 신호 정보 또는 None
        """
        pass

    @classmethod
    @abstractmethod
    async def check_exit_signal(
        cls,
        redis_client,
        position_id: int,
        symbol: str,
        df: pd.DataFrame,
        current_price: Decimal,
        entry_price: Decimal,
        frgn_ntby_qty: int,
        pgtr_ntby_qty: int,
        acml_vol: int
    ) -> Dict:
        """
        매도 신호 체크 (하위 클래스에서 구현 필수)

        Args:
            redis_client: Redis 클라이언트
            position_id: 포지션 ID (SWING_ID)
            symbol: 종목코드
            df: 주가 데이터
            current_price: 현재가
            entry_price: 진입가
            frgn_ntby_qty: 외국인 순매수량
            pgtr_ntby_qty: 프로그램 순매수량
            acml_vol: 누적거래량

        Returns:
            매도 신호 정보 (action: "SELL" or "HOLD")
        """
        pass

    @classmethod
    @abstractmethod
    async def check_second_buy_signal(
        cls,
        db,
        redis_client,
        swing_id: int,
        symbol: str,
        df: pd.DataFrame,
        entry_price: Decimal,
        hold_qty: int,
        current_price: Decimal,
        frgn_ntby_qty: int,
        acml_vol: int,
        prdy_vrss_vol_rate: float
    ) -> Optional[Dict]:
        """
        2차 매수 신호 체크 (하위 클래스에서 구현 필수)

        Args:
            db: 데이터베이스 세션
            redis_client: Redis 클라이언트
            swing_id: 스윙 ID
            symbol: 종목 코드
            df: 주가 데이터
            entry_price: 1차 매수가 (평균 단가)
            hold_qty: 보유 수량
            current_price: 현재가
            frgn_ntby_qty: 외국인 순매수량 (당일 실시간)
            acml_vol: 누적 거래량 (당일 실시간)
            prdy_vrss_vol_rate: 전일 대비 거래량 비율 (%)

        Returns:
            2차 매수 신호 정보 또는 None
        """
        pass