"""
백테스트 전략의 추상 베이스 클래스
"""
from abc import ABC, abstractmethod
from typing import Dict, List
import pandas as pd


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
            final_capital: float
    ) -> Dict:
        """
        백테스트 결과 포맷팅 (공통 로직)

        Args:
            prices_df: 주가 데이터
            params: 파라미터
            trades: 거래 내역
            final_capital: 최종 자본금

        Returns:
            포맷팅된 결과 딕셔너리
        """
        initial_capital = params["swing_amount"]
        total_return = ((final_capital - initial_capital) / initial_capital) * 100

        return {
            "strategy_name": self.name,
            "start_date": str(prices_df["STCK_BSOP_DATE"].min()),
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
