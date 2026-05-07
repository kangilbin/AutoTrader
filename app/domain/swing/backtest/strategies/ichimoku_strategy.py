"""
일목균형표 기반 백테스트 전략
"""
import pandas as pd
from typing import Dict
from .base_strategy import BacktestStrategy
from app.domain.swing.tech_analysis import ichimoku_swing_signals


class IchimokuStrategy(BacktestStrategy):
    """일목균형표 전략"""

    def __init__(self):
        super().__init__("일목균형표 전략")

    def compute(self, prices_df: pd.DataFrame, params: Dict) -> Dict:
        """
        일목균형표 전략 백테스트 실행

        Args:
            prices_df: 주가 데이터
            params: 전략 파라미터

        Returns:
            백테스트 결과
        """
        df = prices_df.copy()

        initial_capital = params["init_amount"]
        buy_ratio = 0.7
        sell_ratio = 0.5
        eval_start = params["eval_start"]

        eval_df = df[df["STCK_BSOP_DATE"] >= eval_start].copy()

        trades = []
        current_capital = initial_capital
        total_capital = initial_capital

        # 일목균형표 신호에 따른 매매 로직
        for i in range(len(eval_df)):
            full_idx = df.index.get_loc(eval_df.index[i])
            current_data = df.iloc[: full_idx + 1]

            # tech_analysis의 ichimoku_swing_signals 사용
            first_buy, second_buy, first_sell, stop_loss = ichimoku_swing_signals(current_data)

            current_price = current_data['STCK_CLPR'].iloc[-1]
            current_date = current_data['STCK_BSOP_DATE'].iloc[-1]

            # 현재 포지션 계산
            total_bought = sum(t['quantity'] for t in trades if t['action'] == 'BUY')
            total_sold = sum(t['quantity'] for t in trades if t['action'] == 'SELL')
            total_quantity = total_bought - total_sold

            # 1. 손절 체크 (최우선!)
            if stop_loss and total_quantity > 0:
                sell_amount = total_quantity * current_price
                current_capital += sell_amount

                curr_qty, curr_avg_cost = self._calculate_position_state(trades)
                realized_pnl = (current_price - curr_avg_cost) * total_quantity
                realized_pnl_pct = ((current_price / curr_avg_cost) - 1) * 100 if curr_avg_cost > 0 else 0.0

                trades.append({
                    'date': current_date,
                    'action': 'SELL',
                    'quantity': total_quantity,
                    'price': float(current_price),
                    'amount': float(sell_amount),
                    'current_capital': float(current_capital),
                    'realized_pnl': float(realized_pnl),
                    'realized_pnl_pct': round(realized_pnl_pct, 2),
                    'reason': '🚨 손절 (일목균형표 기준선/구름 하단 - 1.5×ATR)'
                })
                continue  # 다른 신호 무시

            # 1차 매수 신호 (TK 골든크로스 + 구름 상단 + 치코우)
            if first_buy and current_capital > 0 and total_quantity == 0:
                buy_amount = total_capital * buy_ratio
                buy_quantity = int(buy_amount / current_price)
                executed_amount = buy_quantity * current_price

                if buy_quantity > 0:
                    current_capital -= executed_amount
                    trades.append({
                        'date': current_date,
                        'action': 'BUY',
                        'quantity': buy_quantity,
                        'price': float(current_price),
                        'amount': float(executed_amount),
                        'current_capital': float(current_capital),
                        'reason': '1차 매수 (TK 골든크로스 + 구름 상단 돌파)',
                    })

            # 2차 매수 신호 (구름 강세 + 기준선 위)
            elif second_buy and current_capital > 0 and total_quantity > 0:
                buy_amount = min(total_capital * buy_ratio, current_capital)
                buy_quantity = int(buy_amount / current_price)
                executed_amount = buy_quantity * current_price

                if buy_quantity > 0 and executed_amount > 0:
                    current_capital -= executed_amount
                    trades.append({
                        'date': current_date,
                        'action': 'BUY',
                        'quantity': buy_quantity,
                        'price': float(current_price),
                        'amount': float(executed_amount),
                        'current_capital': float(current_capital),
                        'reason': '2차 매수 (구름 강세 + 기준선 위 유지)'
                    })

            # 1차 매도 신호 (TK 데드크로스 + 하락 압력)
            elif first_sell and total_quantity > 0:
                sell_quantity = int(total_quantity * sell_ratio)

                if sell_quantity > 0:
                    sell_amount = sell_quantity * current_price
                    current_capital += sell_amount

                    curr_qty, curr_avg_cost = self._calculate_position_state(trades)
                    realized_pnl = (current_price - curr_avg_cost) * sell_quantity
                    realized_pnl_pct = ((current_price / curr_avg_cost) - 1) * 100 if curr_avg_cost > 0 else 0.0

                    trades.append({
                        'date': current_date,
                        'action': 'SELL',
                        'quantity': sell_quantity,
                        'price': float(current_price),
                        'amount': float(sell_amount),
                        'current_capital': float(current_capital),
                        'realized_pnl': float(realized_pnl),
                        'realized_pnl_pct': round(realized_pnl_pct, 2),
                        'reason': '1차 매도 (TK 데드크로스 + 하락 압력)'
                    })

        # 결과 포맷팅
        result = self._format_result(prices_df, params, trades, current_capital)
        result["parameters"]["ICHIMOKU_PARAMS"] = {
            "TENKAN": 9,
            "KIJUN": 26,
            "SENKOU_B": 52,
            "ATR": 14
        }

        return result
