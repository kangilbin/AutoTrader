
"""
이평선(EMA) 기반 백테스트 전략
"""
import pandas as pd
from typing import Dict
from .base_strategy import BacktestStrategy
from app.swing.tech_analysis import ema_swing_signals


class EMAStrategy(BacktestStrategy):
    """이평선 전략"""
    
    def __init__(self):
        super().__init__("이평선 전략")
    
    def compute(self, prices_df: pd.DataFrame, params: Dict) -> Dict:
        """
        이평선 전략 백테스트 실행
        
        Args:
            prices_df: 주가 데이터
            params: {
                "short_term": 단기 이평선,
                "medium_term": 중기 이평선,
                "long_term": 장기 이평선,
                "swing_amount": 초기 투자금,
                "buy_ratio": 매수 비율,
                "sell_ratio": 매도 비율,
                "eval_start": 평가 시작일,
                ...
            }
            
        Returns:
            백테스트 결과
        """
        short_term = params["short_term"]
        medium_term = params["medium_term"]
        long_term = params["long_term"]
        initial_capital = params["swing_amount"]
        buy_ratio = params["buy_ratio"]
        sell_ratio = params["sell_ratio"]
        eval_start = params["eval_start"]
        
        df = prices_df.copy()
        eval_df = df[df["STCK_BSOP_DATE"] >= eval_start].copy()
        
        trades = []
        total_capital = initial_capital
        current_capital = initial_capital
        
        for i in range(len(eval_df)):
            full_idx = df.index.get_loc(eval_df.index[i])
            current_data = df.iloc[: full_idx + 1]
            
            # 이평선 신호 계산
            first_buy_signal, second_buy_signal, first_sell_signal, second_sell_signal, stop_loss_signal = ema_swing_signals(
                current_data,
                short_term,
                medium_term,
                long_term
            )
            
            current_price = current_data['STCK_CLPR'].iloc[-1]
            current_date = current_data['STCK_BSOP_DATE'].iloc[-1]
            
            # 현재 포지션 계산
            total_bought = sum(t['quantity'] for t in trades if t['action'] == 'BUY')
            total_sold = sum(t['quantity'] for t in trades if t['action'] == 'SELL')
            total_quantity = total_bought - total_sold
            
            # 1차 매수 신호
            if first_buy_signal and current_capital > 0 and total_quantity == 0:
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
                        'reason': '1차 매수 신호 (단기-중기 골든크로스)',
                    })
            
            # 2차 매수 신호
            elif second_buy_signal and current_capital > 0 and total_quantity > 0:
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
                        'reason': '2차 매수 신호 (중기-장기 골든크로스)'
                    })
            
            # 매도 신호
            elif (first_sell_signal or second_sell_signal) and total_quantity > 0:
                curr_qty, curr_avg_cost = self._calculate_position_state(trades)
                
                if first_sell_signal:
                    sell_quantity = int(total_quantity * sell_ratio)
                    if sell_quantity > 0:
                        sell_amount = sell_quantity * current_price
                        realized_pnl = (current_price - curr_avg_cost) * sell_quantity
                        realized_pnl_pct = ((current_price / curr_avg_cost) - 1) * 100 if curr_avg_cost > 0 else 0.0
                        current_capital += sell_amount
                        
                        trades.append({
                            'date': current_date,
                            'action': 'SELL',
                            'quantity': sell_quantity,
                            'price': float(current_price),
                            'amount': float(sell_amount),
                            'current_capital': float(current_capital),
                            'realized_pnl': float(realized_pnl),
                            'realized_pnl_pct': round(realized_pnl_pct, 2),
                            'reason': '1차 매도 신호 (단기-중기 데드크로스)'
                        })
                
                elif second_sell_signal:
                    sell_quantity = curr_qty
                    sell_amount = sell_quantity * current_price
                    realized_pnl = (current_price - curr_avg_cost) * sell_quantity
                    realized_pnl_pct = ((current_price / curr_avg_cost) - 1) * 100 if curr_avg_cost > 0 else 0.0
                    current_capital += sell_amount
                    
                    trades.append({
                        'date': current_date,
                        'action': 'SELL',
                        'quantity': sell_quantity,
                        'price': float(current_price),
                        'amount': float(sell_amount),
                        'current_capital': float(current_capital),
                        'realized_pnl': float(realized_pnl),
                        'realized_pnl_pct': round(realized_pnl_pct, 2),
                        'reason': '2차 매도 신호 - 전량 매도 (중기-장기 데드크로스)'
                    })
        
        # 결과 포맷팅
        result = self._format_result(prices_df, params, trades, current_capital)
        result["parameters"].update({
            "SHORT_TERM": short_term,
            "MEDIUM_TERM": medium_term,
            "LONG_TERM": long_term,
        })
        
        return result
