"""
백테스트 전략의 추상 베이스 클래스
"""
import math
from abc import ABC, abstractmethod
from typing import Dict, List
import pandas as pd

# 한국 주식 호가 단위표
_TICK_TABLE = [
    (2_000,    1),
    (5_000,    5),
    (20_000,   10),
    (50_000,   50),
    (200_000,  100),
    (500_000,  500),
    (float("inf"), 1_000),
]


def tick_size(price: float) -> int:
    """가격대별 호가 단위 반환"""
    for threshold, tick in _TICK_TABLE:
        if price < threshold:
            return tick
    return 1_000


def ceil_tick(price: float) -> float:
    """매수용: 호가 단위 올림 (불리한 방향)"""
    t = tick_size(price)
    return math.ceil(price / t) * t


def floor_tick(price: float) -> float:
    """매도용: 호가 단위 내림 (불리한 방향)"""
    t = tick_size(price)
    return math.floor(price / t) * t


class BacktestStrategy(ABC):
    """백테스트 전략 베이스 클래스"""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def compute(self, prices_df: pd.DataFrame, params: Dict) -> Dict:
        """
        백테스트 실행 (하위 클래스에서 구현 필수)

        Args:
            prices_df: 주가 데이터 DataFrame
            params: 전략 파라미터 딕셔너리

        Returns:
            백테스트 결과 딕셔너리
        """
        pass

    def _calculate_position_state(self, trades: List[Dict]) -> tuple:
        """
        현재 포지션 상태 계산 (공통 로직)

        Args:
            trades: 거래 내역 리스트

        Returns:
            (보유 수량, 평균 단가)
        """
        position_qty = 0
        position_cost = 0.0

        for trade in trades:
            if trade['action'] == 'BUY':
                position_cost += trade['quantity'] * trade['price']
                position_qty += trade['quantity']
            elif trade['action'] == 'SELL' and position_qty > 0:
                avg_cost = position_cost / position_qty
                sell_qty = trade['quantity']
                position_cost -= avg_cost * sell_qty
                position_qty -= sell_qty

        avg_cost_now = (position_cost / position_qty) if position_qty > 0 else 0.0
        return position_qty, avg_cost_now

    def _format_result(
            self,
            prices_df: pd.DataFrame,
            params: Dict,
            trades: List[Dict],
            final_capital: float,
            eval_df: pd.DataFrame = None
    ) -> Dict:
        """
        백테스트 결과 포맷팅 (공통 로직)

        Args:
            prices_df: 주가 데이터
            params: 파라미터
            trades: 거래 내역
            final_capital: 최종 자본금
            eval_df: 지표 계산된 평가 기간 DataFrame (차트 데이터용)

        Returns:
            포맷팅된 결과 딕셔너리
        """
        initial_capital = params["init_amount"]
        total_return = ((final_capital - initial_capital) / initial_capital) * 100

        result = {
            "strategy_name": self.name,
            "start_date": params["eval_start"].strftime("%Y-%m-%d"),
            "end_date": str(prices_df["STCK_BSOP_DATE"].max()),
            "initial_capital": initial_capital,
            "final_capital": final_capital,
            "total_return": round(total_return, 2),
            "total_trades": len(trades),
            "parameters": {
                "ST_CODE": params["st_code"],
                "SWING_TYPE": params["swing_type"],
            },
            "trades": trades,
        }

        if eval_df is not None and "ema20" in eval_df.columns:
            chart_df = eval_df.copy()
            chart_df["STCK_BSOP_DATE"] = chart_df["STCK_BSOP_DATE"].dt.strftime("%Y%m%d")
            result["price_history"] = chart_df[
                ["STCK_BSOP_DATE", "STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR", "ACML_VOL"]
            ].to_dict(orient="records")
            result["ema20_history"] = chart_df[["STCK_BSOP_DATE", "ema20"]].assign(
                ema20=chart_df["ema20"].round(2).where(chart_df["ema20"].notna(), None)
            ).to_dict(orient="records")

        return result
