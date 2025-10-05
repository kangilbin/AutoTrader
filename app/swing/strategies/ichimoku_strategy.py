"""
일목균형표 기반 백테스트 전략
"""
import pandas as pd
from typing import Dict
from .base_strategy import BacktestStrategy


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
        # TODO: 일목균형표 로직 구현
        # 전환선 = (9일 최고가 + 9일 최저가) / 2
        # 기준선 = (26일 최고가 + 26일 최저가) / 2
        # 선행스팬1 = (전환선 + 기준선) / 2, 26일 선행
        # 선행스팬2 = (52일 최고가 + 52일 최저가) / 2, 26일 선행
        # 후행스팬 = 현재 종가, 26일 후행
        
        df = prices_df.copy()
        
        # 일목균형표 지표 계산
        df = self._calculate_ichimoku(df)
        
        initial_capital = params["swing_amount"]
        buy_ratio = params["buy_ratio"]
        sell_ratio = params["sell_ratio"]
        eval_start = params["eval_start"]
        
        eval_df = df[df["STCK_BSOP_DATE"] >= eval_start].copy()
        
        trades = []
        current_capital = initial_capital
        total_capital = initial_capital
        
        # TODO: 일목균형표 신호에 따른 매매 로직
        # 예시: 전환선이 기준선을 상향 돌파 시 매수
        #       전환선이 기준선을 하향 돌파 시 매도
        
        for i in range(len(eval_df)):
            current_row = eval_df.iloc[i]
            current_price = current_row['STCK_CLPR']
            current_date = current_row['STCK_BSOP_DATE']
            
            # 매매 신호 판단 (일목균형표 로직)
            buy_signal, sell_signal = self._get_ichimoku_signals(eval_df, i)
            
            # 현재 포지션
            total_bought = sum(t['quantity'] for t in trades if t['action'] == 'BUY')
            total_sold = sum(t['quantity'] for t in trades if t['action'] == 'SELL')
            total_quantity = total_bought - total_sold
            
            # 매수 신호
            if buy_signal and current_capital > 0:
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
                        'reason': '일목균형표 매수 신호',
                    })
            
            # 매도 신호
            elif sell_signal and total_quantity > 0:
                sell_quantity = total_quantity
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
                    'reason': '일목균형표 매도 신호'
                })
        
        # 결과 포맷팅
        result = self._format_result(prices_df, params, trades, current_capital)
        result["parameters"]["ICHIMOKU_PARAMS"] = "기본값 (9, 26, 52)"
        
        return result
    
    def _calculate_ichimoku(self, df: pd.DataFrame) -> pd.DataFrame:
        """일목균형표 지표 계산"""
        # 전환선 (Tenkan-sen): 9일
        high_9 = df['STCK_HGPR'].rolling(window=9).max()
        low_9 = df['STCK_LWPR'].rolling(window=9).min()
        df['tenkan_sen'] = (high_9 + low_9) / 2
        
        # 기준선 (Kijun-sen): 26일
        high_26 = df['STCK_HGPR'].rolling(window=26).max()
        low_26 = df['STCK_LWPR'].rolling(window=26).min()
        df['kijun_sen'] = (high_26 + low_26) / 2
        
        # 선행스팬1 (Senkou Span A): 26일 선행
        df['senkou_span_a'] = ((df['tenkan_sen'] + df['kijun_sen']) / 2).shift(26)
        
        # 선행스팬2 (Senkou Span B): 52일, 26일 선행
        high_52 = df['STCK_HGPR'].rolling(window=52).max()
        low_52 = df['STCK_LWPR'].rolling(window=52).min()
        df['senkou_span_b'] = ((high_52 + low_52) / 2).shift(26)
        
        # 후행스팬 (Chikou Span): 26일 후행
        df['chikou_span'] = df['STCK_CLPR'].shift(-26)
        
        return df
    
    def _get_ichimoku_signals(self, df: pd.DataFrame, index: int) -> tuple:
        """일목균형표 매매 신호 판단"""
        if index < 1:
            return False, False
        
        current = df.iloc[index]
        previous = df.iloc[index - 1]
        
        # 매수 신호: 전환선이 기준선을 상향 돌파
        buy_signal = (
            previous['tenkan_sen'] <= previous['kijun_sen'] and
            current['tenkan_sen'] > current['kijun_sen'] and
            current['STCK_CLPR'] > current['senkou_span_a'] and
            current['STCK_CLPR'] > current['senkou_span_b']
        )
        
        # 매도 신호: 전환선이 기준선을 하향 돌파
        sell_signal = (
            previous['tenkan_sen'] >= previous['kijun_sen'] and
            current['tenkan_sen'] < current['kijun_sen']
        )
        
        return buy_signal, sell_signal
