"""
단일 20EMA 백테스팅 전략 (Single EMA Backtest Strategy) - 하이브리드 매도 로직 적용

실전 매매 전략과 동일한 이원화된 하이브리드 매도 로직을 적용하여 백테스트의 정확도를 높입니다.

**매수 조건 (Entry Conditions):**
*   **하락장 필터:** 20 EMA < 120 EMA 시 매수 금지 (신규 진입 및 2차 매수 모두 차단)

**매도 조건 (Exit Conditions):**

**[1차 방어선] 즉시 매도 (일일 저가 기준, OR 조건)**
1.  **고정 손절:** 저가가 진입가 대비 -3% 도달
2.  **EMA-ATR 동적 손절:** 저가가 EMA - (ATR × 1.0) 도달

**[2차 방어선] 장 마감 매도 (일일 종가 기준, 교차 검증)**
*   **시간 윈도우:** 최근 3거래일 이내 발생한 신호만 유효
1.  **1차 분할 매도:** 아래 3개 조건 중 **2개 이상** 충족 시
    -   EMA 종가 이탈
    -   추세 약화 (ADX/DMI 2일 연속 약세)
    -   수급 이탈 (OBV z-score)
2.  **2차 전량 매도:** 1차 매도 후, 아래 조건 중 하나라도 충족 시
    -   -3% 고정 손절 도달
    -   장 마감 시, 위 3개 조건이 **모두** 충족
    -   1차 매도가 대비 -2% 추가 하락
"""
import pandas as pd
from typing import Dict, List, Optional, Tuple
from .base_strategy import BacktestStrategy
from app.domain.swing.indicators import TechnicalIndicators
from datetime import datetime
import logging

class SingleEMABacktestStrategy(BacktestStrategy):
    """단일 20EMA 백테스팅 전략 (하이브리드 매도 로직)"""

    # === 기본 파라미터 ===
    EMA_PERIOD = 20
    EMA_LONG_PERIOD = 120  # 장기 EMA (하락장 판단용)
    MAX_SURGE_RATIO = 0.05
    CONSECUTIVE_REQUIRED = 1

    # === 매도 조건 파라미터 ===
    # [1차 방어선]
    STOP_LOSS_FIXED = -0.03
    ATR_MULTIPLIER = 1.0
    # [2차 방어선]
    OBV_Z_SELL_THRESHOLD = -1.0
    EOD_SIGNAL_WINDOW_DAYS = 3
    EOD_TREND_WEAK_DAYS = 2
    SECONDARY_SELL_ADDITIONAL_DROP = -0.02
    
    # === 매수 조건 파라미터 ===
    OBV_Z_BUY_THRESHOLD = 1.0
    OBV_LOOKBACK = 7
    MAX_GAP_RATIO = 0.05

    # === 2차 매수 조건 ===
    # [시나리오 A] 추세 강화형
    SECOND_BUY_PRICE_GAIN_MIN = 0.02
    SECOND_BUY_PRICE_GAIN_MAX = 0.08
    SECOND_BUY_OBV_THRESHOLD = 1.2
    SECOND_BUY_SAFETY_MARGIN = 0.04

    # [시나리오 B] 눌림목 매수형 (건강한 조정 후 반등)
    PULLBACK_BUY_DROP_MIN = -0.025            # 진입가 대비 최대 하락폭 (-2.5%, 손절 전 안전마진)
    PULLBACK_BUY_DROP_MAX = -0.01             # 진입가 대비 최소 하락폭 (-1.0%, 조정 인정 최소선)
    PULLBACK_BUY_EMA_TOLERANCE = 0.98         # EMA 대비 허용 하한 (EMA × 0.98)
    PULLBACK_BUY_OBV_MIN = 0.0                # 수급 최소 요구치 (중립 이상)
    PULLBACK_BUY_REBOUND_RATIO = 1.01         # 저점 대비 반등 비율 (1%)

    # === 거래 비용 ===
    COMMISSION_RATE = 0.00147
    TAX_RATE = 0.0020

    def __init__(self):
        super().__init__("단일 20EMA 전략 (하이브리드)")

    def compute(self, prices_df: pd.DataFrame, params: Dict) -> Dict:
        initial_capital = params["init_amount"]
        buy_ratio = params["buy_ratio"]
        sell_ratio = params["sell_ratio"]
        eval_start = pd.to_datetime(params["eval_start"])

        df = self._prepare_data(prices_df)
        df = self._calculate_indicators(df)
        eval_df = df[df["STCK_BSOP_DATE"] >= eval_start].copy()

        trades: List[Dict] = []
        current_capital = initial_capital

        # === 상태 추적 변수 ===
        position_status = None  # None, 'BUY_COMPLETE', 'SELL_PRIMARY'
        entry_price = 0.0
        first_sell_price = 0.0
        buy_count = 0
        
        # EOD 신호 발생일 추적 (시간 윈도우용)
        eod_signal_dates = {
            'ema_breach': None,
            'trend_weak': None,
            'supply_weak': None
        }

        for i in range(1, len(eval_df)):
            row = eval_df.iloc[i]
            prev_row = eval_df.iloc[i-1]
            current_date = row["STCK_BSOP_DATE"]

            if current_date > datetime(2025, 12, 18):
                logging.info("확인해볼까용")
            # === 1단계: 포지션 보유 시 매도 조건 체크 ===
            if position_status in ['BUY_COMPLETE', 'SELL_PRIMARY']:

                # [1차 방어선] 즉시 매도 조건 체크 (저가 기준)
                immediate_sell_signal, reason, sell_price = self._check_immediate_sell_conditions(row, entry_price)

                if immediate_sell_signal:
                    current_capital = self._execute_sell(trades, current_date, sell_price, current_capital, reason, sell_all=True)

                    # 상태 초기화
                    position_status, entry_price, first_sell_price, buy_count = None, 0.0, 0.0, 0
                    eod_signal_dates = {k: None for k in eod_signal_dates}
                    continue

                # [2차 방어선] 장 마감 매도 조건 체크 (종가 기준)
                eod_sell_action, reason = self._update_and_check_eod_sell_signals(
                    row, prev_row, current_date, eod_signal_dates, position_status, entry_price, first_sell_price
                )

                if eod_sell_action == 'SELL_PRIMARY':
                    sell_price = row["STCK_CLPR"]
                    current_capital = self._execute_sell(trades, current_date, sell_price, current_capital, reason, sell_ratio=sell_ratio)
                    position_status = 'SELL_PRIMARY'
                    first_sell_price = sell_price

                elif eod_sell_action == 'SELL_ALL':
                    sell_price = row["STCK_CLPR"]
                    current_capital = self._execute_sell(trades, current_date, sell_price, current_capital, reason, sell_all=True)
                    position_status, entry_price, first_sell_price, buy_count = None, 0.0, 0.0, 0
                    eod_signal_dates = {k: None for k in eod_signal_dates}

            # === 2단계: 포지션 미보유 또는 추가매수 가능 시 매수 조건 체크 ===
            can_buy = position_status is None or (position_status == 'BUY_COMPLETE' and buy_count < 2)
            if can_buy and current_capital > 0:
                is_entry = False
                buy_amount = 0  # 초기화

                if position_status is None:
                    if self._check_entry_conditions(row):
                       is_entry = True
                       buy_amount = current_capital * buy_ratio
                else: # 2차 매수
                    if self._check_second_buy_conditions(row, entry_price):
                        is_entry = True
                        buy_amount = current_capital

                if is_entry and buy_amount > 0:
                    buy_price = row["STCK_CLPR"]
                    buy_quantity = int(buy_amount / buy_price)

                    if buy_quantity > 0:
                        if buy_count == 0: # 1차 매수
                            entry_price = buy_price
                        else: # 2차 매수
                            curr_qty, curr_avg_cost = self._calculate_position_state(trades)
                            total_cost = (curr_avg_cost * curr_qty) + (buy_quantity * buy_price)
                            total_qty = curr_qty + buy_quantity
                            entry_price = total_cost / total_qty

                        current_capital = self._execute_buy(trades, current_date, buy_price, buy_quantity, current_capital, f"{buy_count+1}차 매수")
                        buy_count += 1
                        position_status = 'BUY_COMPLETE'
                        # 매수 시 모든 매도 신호 리셋
                        eod_signal_dates = {k: None for k in eod_signal_dates}

        # 최종 청산 및 결과 포맷팅
        final_capital = self._liquidate_final_position(trades, eval_df, current_capital)
        result = self._format_result(prices_df, params, trades, final_capital)
        return result

    def _check_immediate_sell_conditions(self, row: pd.Series, entry_price: float) -> Tuple[bool, str, float]:
        """[1차 방어선] 저가 기준 즉시 매도 조건 체크

        Returns:
            (신호발생여부, 사유, 매도가)
        """
        low_price = row["STCK_LWPR"]
        open_price = row["STCK_OPRC"]

        # 1. 고정 손절
        fixed_stop = entry_price * (1 + self.STOP_LOSS_FIXED)
        if low_price <= fixed_stop:
            # 갭하락 시 시가에 매도, 그 외 손절가에 매도
            sell_price = min(fixed_stop, open_price)
            return True, f"고정손절({self.STOP_LOSS_FIXED*100:.2f}%)", sell_price

        # 2. EMA-ATR 동적 손절 (NaN 체크)
        if pd.notna(row["ema_20"]) and pd.notna(row["atr"]):
            ema_stop_loss = row["ema_20"] - (row["atr"] * self.ATR_MULTIPLIER)
            if low_price <= ema_stop_loss:
                # 갭하락 시 시가에 매도, 그 외 손절가에 매도
                sell_price = min(ema_stop_loss, open_price)
                return True, "EMA-ATR손절", sell_price

        return False, "", 0.0

    def _update_and_check_eod_sell_signals(self, row, prev_row, current_date, eod_signal_dates, position_status, entry_price, first_sell_price) -> Tuple[Optional[str], str]:
        """[2차 방어선] EOD 매도 조건 교차 검증"""
        
        # 2차 전량 매도 조건 (1차 분할매도 상태일 때)
        if position_status == 'SELL_PRIMARY':
            if row['STCK_CLPR'] <= entry_price * (1 + self.STOP_LOSS_FIXED):
                 return "SELL_ALL", "2차매도(고정손절)"
            if row['STCK_CLPR'] <= first_sell_price * (1 + self.SECONDARY_SELL_ADDITIONAL_DROP):
                 return "SELL_ALL", f"2차매도(추가하락 {self.SECONDARY_SELL_ADDITIONAL_DROP*100:.2f}%)"

        # EOD 신호 발생 여부 업데이트
        if self._check_ema_breach_eod(row): eod_signal_dates['ema_breach'] = current_date
        if self._check_trend_weakness_eod(row, prev_row): eod_signal_dates['trend_weak'] = current_date
        if self._check_supply_weakness_eod(row): eod_signal_dates['supply_weak'] = current_date
        
        # 시간 윈도우 내 유효 신호 카운트
        valid_signals = []
        for signal, trigger_date in eod_signal_dates.items():
            if trigger_date and (current_date - trigger_date).days < self.EOD_SIGNAL_WINDOW_DAYS:
                valid_signals.append(signal)
        
        # 매도 결정
        if position_status == 'SELL_PRIMARY' and len(valid_signals) >= 3:
            return "SELL_ALL", f"2차매도(모든 EOD 신호 충족: {valid_signals})"
        
        if position_status == 'BUY_COMPLETE' and len(valid_signals) >= 2:
            return "SELL_PRIMARY", f"1차매도({len(valid_signals)}/3 충족: {valid_signals})"
            
        return None, ""

    @staticmethod
    def _check_ema_breach_eod(row: pd.Series) -> bool:
        if pd.isna(row['ema_20']):
            return False
        return row['STCK_CLPR'] < row['ema_20']

    @staticmethod
    def _check_trend_weakness_eod(row: pd.Series, prev_row: pd.Series) -> bool:
        # NaN 체크
        required_cols = ['adx', 'minus_di', 'plus_di']
        if any(pd.isna(row[col]) for col in required_cols):
            return False
        if any(pd.isna(prev_row[col]) for col in required_cols):
            return False
        # 백테스트에서는 데이터프레임의 이전 row를 활용하여 2일 연속을 체크
        is_today_weak = row['adx'] < 20 and row['minus_di'] > row['plus_di']
        is_yesterday_weak = prev_row['adx'] < 20 and prev_row['minus_di'] > prev_row['plus_di']
        return is_today_weak and is_yesterday_weak

    def _check_supply_weakness_eod(self, row: pd.Series) -> bool:
        if pd.isna(row['obv_z']):
            return False
        return row['obv_z'] < self.OBV_Z_SELL_THRESHOLD

    def _is_bearish_market(self, row: pd.Series) -> bool:
        """
        하락장 판단: 20 EMA가 120 EMA 아래로 내려간 경우

        Returns:
            True: 하락장 (매수 금지)
            False: 상승장/횡보장 (매수 허용)
        """
        if pd.isna(row.get('ema_20')) or pd.isna(row.get('ema_120')):
            return False  # 지표 부족 시 매수 허용 (초기 데이터)

        return row['ema_20'] < row['ema_120']

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = TechnicalIndicators.prepare_indicators_from_df(
            df,
            ema_period=self.EMA_PERIOD,
            atr_period=14,
            adx_period=14,
            obv_lookback=self.OBV_LOOKBACK
        )
        df["daily_return"] = df["STCK_CLPR"].pct_change()

        # 장기 EMA (120) 추가 - 하락장 판단용
        close = df["STCK_CLPR"].values.astype(float)
        ema_long = TechnicalIndicators.calculate_ema(close, self.EMA_LONG_PERIOD)
        if ema_long is not None:
            df["ema_120"] = ema_long

        return df

    def _check_entry_conditions(self, row: pd.Series) -> bool:
        if any(pd.isna(row[col]) for col in ["ema_20", "obv_z", "gap_ratio", "plus_di", "minus_di", "daily_return"]):
            return False

        # 하락장 필터: 20 EMA < 120 EMA 시 매수 금지
        if self._is_bearish_market(row):
            return False

        price_near_ema = row["STCK_CLPR"] >= row["ema_20"] * 0.995
        supply_strong = row["obv_z"] > self.OBV_Z_BUY_THRESHOLD
        # surge_filtered = row["daily_return"] <= self.MAX_SURGE_RATIO
        gap_filtered = row["gap_ratio"] <= self.MAX_GAP_RATIO
        trend_upward = row["plus_di"] > row["minus_di"]

        return all([price_near_ema, supply_strong, gap_filtered, trend_upward])

    def _check_second_buy_conditions(self, row: pd.Series, entry_price: float) -> bool:
        """하이브리드 2차 매수: 추세 강화형 + 조정 매수형"""
        if any(pd.isna(row[col]) for col in ["ema_20", "obv_z", "atr", "plus_di", "minus_di"]):
            return False

        # 하락장 필터: 20 EMA < 120 EMA 시 2차 매수 금지
        if self._is_bearish_market(row):
            return False

        current_price = row["STCK_CLPR"]
        price_change = (current_price - entry_price) / entry_price

        # === 시나리오 A: 추세 강화형 (2~8% 상승) ===
        if self.SECOND_BUY_PRICE_GAIN_MIN <= price_change <= self.SECOND_BUY_PRICE_GAIN_MAX:
            if current_price > row["ema_20"] and row["obv_z"] >= self.SECOND_BUY_OBV_THRESHOLD:
                stop_loss_price = entry_price * (1 + self.STOP_LOSS_FIXED)
                safety_threshold = stop_loss_price * (1 + self.SECOND_BUY_SAFETY_MARGIN)
                if current_price >= safety_threshold:
                    return True

        # === 시나리오 B: 눌림목 매수 (건강한 조정 후 반등) ===
        # 가격 조건: 진입가 대비 -2.5% ~ -1.0% 하락 (손절 -3% 전 안전마진 확보)
        if self.PULLBACK_BUY_DROP_MIN <= price_change <= self.PULLBACK_BUY_DROP_MAX:
            # 조건 1: EMA 지지 (EMA 2% 이내)
            if current_price >= row["ema_20"] * self.PULLBACK_BUY_EMA_TOLERANCE:
                # 조건 2: 수급 유지 (중립 이상)
                if row["obv_z"] > self.PULLBACK_BUY_OBV_MIN:
                    # 조건 3: 추세 유지
                    if row["plus_di"] > row["minus_di"]:
                        # 조건 4: 반등 신호 (저점 대비 1% 이상 회복)
                        if current_price >= row["STCK_LWPR"] * self.PULLBACK_BUY_REBOUND_RATIO:
                            return True

        return False
        
    def _prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        if "STCK_BSOP_DATE" in df.columns:
            df["STCK_BSOP_DATE"] = pd.to_datetime(df["STCK_BSOP_DATE"])
            df = df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)
        for col in ["STCK_CLPR", "STCK_HGPR", "STCK_LWPR", "STCK_OPRC", "ACML_VOL"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def _execute_buy(self, trades: List[Dict], date, price: float, quantity: int, current_capital: float, reason: str):
        """매수 실행 및 거래 내역 추가"""
        executed_amount = quantity * price
        commission = executed_amount * self.COMMISSION_RATE
        total_cost = executed_amount + commission

        trades.append({
            'date': date,
            'action': 'BUY',
            'quantity': quantity,
            'price': float(price),
            'amount': float(executed_amount),
            'commission': float(commission),
            'current_capital': float(current_capital - total_cost),
            'reason': reason
        })

        return current_capital - total_cost

    def _execute_sell(self, trades: List[Dict], date, price: float, current_capital: float, reason: str,
                      sell_ratio: float = None, sell_all: bool = False):
        """매도 실행 및 거래 내역 추가"""
        curr_qty, curr_avg_cost = self._calculate_position_state(trades)

        if curr_qty == 0:
            return current_capital

        # 매도 수량 결정
        if sell_all:
            sell_quantity = curr_qty
        elif sell_ratio:
            sell_quantity = int(curr_qty * sell_ratio)
        else:
            sell_quantity = curr_qty

        if sell_quantity == 0:
            return current_capital

        # 매도 금액 및 수수료/세금 계산
        sell_amount = sell_quantity * price
        commission = sell_amount * self.COMMISSION_RATE
        tax = sell_amount * self.TAX_RATE
        net_proceeds = sell_amount - commission - tax

        # 실현 손익 계산
        realized_pnl = (price - curr_avg_cost) * sell_quantity - commission - tax
        realized_pnl_pct = ((price / curr_avg_cost) - 1) * 100 if curr_avg_cost > 0 else 0.0

        trades.append({
            'date': date,
            'action': 'SELL',
            'quantity': sell_quantity,
            'price': float(price),
            'amount': float(sell_amount),
            'commission': float(commission),
            'tax': float(tax),
            'net_proceeds': float(net_proceeds),
            'current_capital': float(current_capital + net_proceeds),
            'realized_pnl': float(realized_pnl),
            'realized_pnl_pct': round(realized_pnl_pct, 2),
            'reason': reason
        })

        return current_capital + net_proceeds

    def _liquidate_final_position(self, trades: List[Dict], eval_df: pd.DataFrame, current_capital: float) -> float:
        """최종 포지션 청산"""
        curr_qty, curr_avg_cost = self._calculate_position_state(trades)

        if curr_qty > 0:
            final_price = eval_df.iloc[-1]["STCK_CLPR"]
            final_date = eval_df.iloc[-1]["STCK_BSOP_DATE"]

            current_capital = self._execute_sell(
                trades,
                final_date,
                final_price,
                current_capital,
                "최종 청산",
                sell_all=True
            )

        return current_capital
