"""
단일 20EMA 백테스팅 전략 (Single EMA Backtest Strategy)

**매수 조건 (Entry Conditions):**
*   **1차 매수 시나리오 A (눌림목 매집):** EMA ± ATR × 0.5, ADX 18~30 + +DI>-DI, OBV 상승
*   **1차 매수 시나리오 B (추세 추종 돌파):** EMA 상향 돌파, +DI>-DI, ADX>15
*   **2차 매수 (통합):** EMA + ATR × (0.5~2.0), ADX >= 20, OBV z-score >= 0.5

**매도 조건 (Exit Conditions):**

**[1차 방어선] 즉시 손절 (일일 저가 기준)**
*   **EMA-ATR 동적 손절:** 저가가 EMA - (ATR × 1.0) 도달 시 전량 매도

**[2차 방어선] trailing stop 익절 (일일 저가 기준)**
*   저가 ≤ 고점(PEAK) - ATR×2.0 시 전량 매도
"""
import pandas as pd
from typing import Dict, List, Optional, Tuple
from .base_strategy import BacktestStrategy
from app.domain.swing.indicators import TechnicalIndicators
from app.domain.swing.trading.strategies.base_single_ema import BaseSingleEMAStrategy

class SingleEMABacktestStrategy(BacktestStrategy, BaseSingleEMAStrategy):
    """단일 20EMA 백테스팅 전략"""

    # === 백테스팅 전용 파라미터 ===
    CONSECUTIVE_REQUIRED = 1     # 백테스팅에서는 연속 확인 불필요

    # === 거래 비용 ===
    COMMISSION_RATE = 0.00147
    TAX_RATE = 0.0020

    def __init__(self):
        super().__init__("단일 20EMA 전략")

    def compute(self, prices_df: pd.DataFrame, params: Dict) -> Dict:
        initial_capital = params["init_amount"]
        eval_start = pd.to_datetime(params["eval_start"])

        df = self._prepare_data(prices_df)
        df = self._calculate_indicators(df)
        eval_df = df[df["STCK_BSOP_DATE"] >= eval_start].copy()

        trades: List[Dict] = []
        current_capital = initial_capital

        # === 상태 추적 변수 ===
        has_position = False
        peak_price = 0.0

        for i in range(2, len(eval_df)):
            row = eval_df.iloc[i]
            prev_row = eval_df.iloc[i-1]
            prev_prev_row = eval_df.iloc[i-2]
            current_date = row["STCK_BSOP_DATE"]

            # === 1단계: 포지션 보유 시 매도 조건 체크 ===
            if has_position:
                peak_price = max(peak_price, row["STCK_HGPR"])

                # [1차 방어선] 즉시 손절 조건 체크 (저가 기준)
                immediate_sell_signal, reasons, sell_price = self._check_immediate_sell_conditions(row)

                if immediate_sell_signal:
                    current_capital = self._execute_sell(trades, current_date, sell_price, current_capital, reasons, sell_all=True)
                    has_position = False
                    peak_price = 0.0
                    continue

                # [2차 방어선] trailing stop 익절 (저가 기준)
                trailing_sell, reasons = self._check_trailing_stop(row, peak_price)

                if trailing_sell:
                    sell_price = row["STCK_CLPR"]
                    current_capital = self._execute_sell(trades, current_date, sell_price, current_capital, reasons, sell_all=True)
                    has_position = False
                    peak_price = 0.0
                    continue

            # === 2단계: 포지션 미보유 시 매수 조건 체크 (ATR 기반 포지션 사이징) ===
            if not has_position and current_capital > 0:
                matched, signal_reasons = self._check_entry_conditions(row, prev_row, prev_prev_row)

                if matched:
                    buy_price = row["STCK_CLPR"]
                    atr = row.get("atr", 0)

                    # ATR 기반 수량 계산: min(Equity×Risk%/ATR, Equity×MaxPos%/Price)
                    if pd.notna(atr) and atr > 0 and buy_price > 0:
                        risk_qty = current_capital * self.RISK_PCT / atr
                        max_qty = current_capital * self.MAX_POSITION_PCT / buy_price
                        buy_quantity = int(min(risk_qty, max_qty))
                    else:
                        buy_quantity = int(current_capital * self.MAX_POSITION_PCT / buy_price)

                    if buy_quantity > 0:
                        reasons = ["매수"] + signal_reasons
                        current_capital = self._execute_buy(trades, current_date, buy_price, buy_quantity, current_capital, reasons)
                        has_position = True
                        peak_price = buy_price

        # 최종 청산 및 결과 포맷팅
        final_capital = self._liquidate_final_position(trades, eval_df, current_capital)
        result = self._format_result(prices_df, params, trades, final_capital)
        return result

    def _check_immediate_sell_conditions(self, row: pd.Series) -> Tuple[bool, List[str], float]:
        """[1차 방어선] 저가 기준 즉시 매도 조건 체크

        Returns:
            (신호발생여부, 사유 리스트, 매도가)
        """
        low_price = row["STCK_LWPR"]

        # EMA-ATR 동적 손절 (NaN 체크 + ATR=0 가드)
        if pd.notna(row["ema_20"]) and pd.notna(row["atr"]) and row["atr"] > 0:
            ema_stop_loss = row["ema_20"] - (row["atr"] * self.ATR_MULTIPLIER)
            if low_price <= ema_stop_loss:
                return True, ["추세 이탈 손절"], ema_stop_loss

        return False, [], 0.0

    def _check_trailing_stop(self, row, peak_price) -> Tuple[bool, List[str]]:
        """[2차 방어선] trailing stop 익절

        고점 대비 ATR×2.0 이상 하락 시 전량 매도 (게이트 조건 없음)
        """
        if peak_price <= 0:
            return False, []

        low_price = row["STCK_LWPR"]
        atr = row.get("atr", 0)

        if pd.notna(atr) and atr > 0:
            stop_price = int(peak_price - atr * self.TRAILING_STOP_ATR_MULT)
        else:
            stop_price = int(peak_price * (1 - self.TRAILING_STOP_FALLBACK_PCT / 100))

        if low_price <= stop_price:
            drawdown_pct = round((peak_price - low_price) / peak_price * 100, 1)
            reasons = ["익절", f"고점대비 -{drawdown_pct}%", f"익절가 {stop_price:.0f}원"]
            return True, reasons

        return False, []

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """지표 계산 (공통 메서드 사용)"""
        return TechnicalIndicators.prepare_full_indicators_for_single_ema(
            df,
            ema_short=self.EMA_PERIOD,
            ema_long=120,
            atr_period=14,
            adx_period=14,
            obv_lookback=self.OBV_LOOKBACK
        )

    def _check_entry_conditions(self, row: pd.Series, prev_row: pd.Series = None, i_minus_2: pd.Series = None) -> Tuple[bool, List[str]]:
        """1차 매수 진입: 시나리오 A(눌림목 매집) + 시나리오 B(추세 추종 돌파)"""
        required_cols = ["ema_20", "obv_z", "plus_di", "minus_di", "daily_return", "adx", "atr"]
        if any(pd.isna(row[col]) for col in required_cols):
            return False, []

        if prev_row is None:
            return False, []

        current_price = row["STCK_CLPR"]

        # === 공통 필터 ===
        surge_filtered = abs(row["daily_return"]) <= self.MAX_SURGE_RATIO
        if not surge_filtered:
            return False, []

        # 전일 EMA20 상승 필터 (prev_row = 어제, 어제 ema_20 vs 전전일 ema_20 비교)
        if i_minus_2 is not None and not pd.isna(i_minus_2.get("ema_20")):
            if not (prev_row["ema_20"] > i_minus_2["ema_20"]):
                return False, []
        else:
            return False, []

        # === 시나리오 A: 눌림목 매집 진입 ===
        accum_lower = row["ema_20"] + (row["atr"] * self.ACCUM_ENTRY_ATR_LOWER)
        accum_upper = row["ema_20"] + (row["atr"] * self.ACCUM_ENTRY_ATR_UPPER)


        if accum_lower <= current_price <= accum_upper:
            obv_accumulating = (row["obv_z"] > self.ACCUM_ENTRY_OBV_MIN) and (row["obv_z"] > prev_row["obv_z"])
            adx_mid_range = self.ACCUM_ENTRY_ADX_MIN <= row["adx"] <= self.ACCUM_ENTRY_ADX_MAX
            ema_rising = row["ema_20"] > prev_row["ema_20"]
            trend_direction = row["plus_di"] > row["minus_di"]

            if obv_accumulating and adx_mid_range and ema_rising and trend_direction:
                return True, ["눌림목매집", "EMA근접", "OBV양호", "추세상승"]

        # === 시나리오 B: 추세 추종 EMA 돌파 진입 ===
        ema_rising = row["ema_20"] > prev_row["ema_20"]
        price_above_ema = current_price > row["ema_20"]
        within_gap_limit = current_price <= row["ema_20"] * self.BREAKOUT_ENTRY_GAP_MAX

        if ema_rising and price_above_ema and within_gap_limit:
            trend_direction = row["plus_di"] > row["minus_di"]
            adx_sufficient = row["adx"] > self.BREAKOUT_ENTRY_ADX_MIN  # 최소 추세 강도
            obv_positive = row["obv_z"] > self.BREAKOUT_ENTRY_OBV_MIN

            if trend_direction and adx_sufficient and obv_positive:
                return True, ["EMA돌파", "상향돌파", "추세확인", "거래량동반"]

        return False, []

    def _check_second_buy_conditions(self, row: pd.Series, prev_row: pd.Series = None, i_minus_2: pd.Series = None) -> Tuple[bool, List[str]]:
        """2차 매수: 추세 안정화 확인 후 추가 매수 (단일 조건)"""
        if any(pd.isna(row[col]) for col in ["ema_20", "obv_z", "atr", "adx", "plus_di", "minus_di"]):
            return False, []

        if prev_row is None:
            return False, []

        # 전일 EMA20 상승 필터
        if i_minus_2 is not None and not pd.isna(i_minus_2.get("ema_20")):
            if not (prev_row["ema_20"] > i_minus_2["ema_20"]):
                return False, []
        else:
            return False, []

        current_price = row["STCK_CLPR"]

        # 가격 위치: EMA 이상 ~ EMA + ATR × 2.0 (추세 확인 + 과열 방지)
        lower = row["ema_20"] + (row["atr"] * self.SECOND_BUY_ATR_LOWER)
        upper = row["ema_20"] + (row["atr"] * self.SECOND_BUY_ATR_UPPER)

        if lower <= current_price <= upper:
            # 추세 안정: ADX >= 20 + 상승 방향
            if row["adx"] >= self.SECOND_BUY_ADX_MIN and row["plus_di"] > row["minus_di"]:
                # 수급 확인: OBV z-score
                if row["obv_z"] >= self.SECOND_BUY_OBV_MIN:
                    return True, ["추세안정", "EMA상단", "ADX확인", "OBV양전"]

        return False, []
        
    def _prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        if "STCK_BSOP_DATE" in df.columns:
            df["STCK_BSOP_DATE"] = pd.to_datetime(df["STCK_BSOP_DATE"], format="%Y%m%d")
            df = df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)
        for col in ["STCK_CLPR", "STCK_HGPR", "STCK_LWPR", "STCK_OPRC", "ACML_VOL"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def _execute_buy(self, trades: List[Dict], date, price: float, quantity: int, current_capital: float, reasons: List[str]):
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
            'reasons': reasons
        })

        return current_capital - total_cost

    def _execute_sell(self, trades: List[Dict], date, price: float, current_capital: float, reasons: List[str],
                      sell_all: bool = False):
        """매도 실행 및 거래 내역 추가 (항상 전량 매도)"""
        curr_qty, curr_avg_cost = self._calculate_position_state(trades)

        if curr_qty == 0:
            return current_capital

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
            'reasons': reasons
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
                ["최종 청산"],
                sell_all=True
            )

        return current_capital
