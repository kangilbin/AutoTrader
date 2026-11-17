"""
단일 20EMA 백테스팅 전략 (Single EMA Backtest Strategy) - 하이브리드 매도 로직 적용

실전 매매 전략과 동일한 이원화된 하이브리드 매도 로직을 적용하여 백테스트의 정확도를 높입니다.

**매수 조건 (Entry Conditions):**
*   **1차 매수 시나리오 A (눌림목 매집):** EMA ± ATR × 0.5, ADX 18~30 + +DI>-DI, OBV 상승
*   **1차 매수 시나리오 B (추세 추종 돌파):** EMA 상향 돌파, +DI>-DI, ADX>15
*   **2차 매수 (통합):** EMA + ATR × (0.5~2.0), ADX >= 20, OBV z-score >= 0.5

**매도 조건 (Exit Conditions):**

**[1차 방어선] 즉시 매도 (일일 저가 기준)**
*   **EMA-ATR 동적 손절:** 저가가 EMA - (ATR × 1.0) 도달

**[2차 방어선] EOD 조건부 trailing stop (일일 종가 기준)**
*   **추세 약화 AND 수급 약화 동시 충족 시:**
*   추세 약화: (+DI - -DI) 격차 2일 연속 감소
*   수급 약화: OBV z-score 감소 (전일 대비)
1.  **1차 분할 매도:** BUY_COMPLETE 상태 + 고점(고가) 대비 저가 ≥ ATR×2.0 하락 (최소 3%)
2.  **2차 전량 매도:** SELL_PRIMARY 상태 + 고점(고가) 대비 저가 ≥ ATR×3.0 하락 (최소 5%)
"""
import pandas as pd
from typing import Dict, List, Optional, Tuple
from .base_strategy import BacktestStrategy
from app.domain.swing.indicators import TechnicalIndicators
from app.domain.swing.trading.strategies.base_single_ema import BaseSingleEMAStrategy

class SingleEMABacktestStrategy(BacktestStrategy, BaseSingleEMAStrategy):
    """단일 20EMA 백테스팅 전략 (하이브리드 매도 로직)"""

    # === 백테스팅 전용 파라미터 ===
    CONSECUTIVE_REQUIRED = 1     # 백테스팅에서는 연속 확인 불필요

    # === 거래 비용 ===
    COMMISSION_RATE = 0.00147
    TAX_RATE = 0.0020

    def __init__(self):
        super().__init__("단일 20EMA 전략")

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
        buy_count = 0
        peak_price = 0.0  # 매수 이후 최고 종가

        for i in range(2, len(eval_df)):
            row = eval_df.iloc[i]
            prev_row = eval_df.iloc[i-1]
            prev_prev_row = eval_df.iloc[i-2]
            current_date = row["STCK_BSOP_DATE"]

            # === 1단계: 포지션 보유 시 매도 조건 체크 ===
            if position_status in ['BUY_COMPLETE', 'SELL_PRIMARY']:
                peak_price = max(peak_price, row["STCK_HGPR"])

                # [1차 방어선] 즉시 매도 조건 체크 (저가 기준)
                immediate_sell_signal, reason, sell_price = self._check_immediate_sell_conditions(row)

                if immediate_sell_signal:
                    current_capital = self._execute_sell(trades, current_date, sell_price, current_capital, reason, sell_all=True)

                    # 상태 초기화
                    position_status, buy_count = None, 0
                    peak_price = 0.0
                    continue

                # [2차 방어선] EOD 조건부 trailing stop (종가 기준)
                eod_sell_action, reason = self._check_eod_trailing_stop(
                    row, prev_row, prev_prev_row, peak_price, position_status
                )

                if eod_sell_action == 'SELL_PRIMARY':
                    sell_price = row["STCK_CLPR"]
                    current_capital = self._execute_sell(trades, current_date, sell_price, current_capital, reason, sell_ratio=sell_ratio)
                    position_status = 'SELL_PRIMARY'

                elif eod_sell_action == 'SELL_ALL':
                    sell_price = row["STCK_CLPR"]
                    current_capital = self._execute_sell(trades, current_date, sell_price, current_capital, reason, sell_all=True)
                    position_status, buy_count = None, 0
                    peak_price = 0.0

                elif position_status == 'SELL_PRIMARY':
                    # 매도 신호 없음: 재진입 조건 체크 (실전 SIGNAL 3 재진입과 동일)
                    matched, signal_reason = self._check_entry_conditions(row, prev_row)
                    if matched and current_capital > 0:
                        buy_price = row["STCK_CLPR"]
                        buy_amount = current_capital * buy_ratio
                        buy_quantity = int(buy_amount / buy_price)
                        if buy_quantity > 0:
                            current_capital = self._execute_buy(trades, current_date, buy_price, buy_quantity, current_capital, f"재진입 매수({signal_reason})")
                            buy_count = 1
                            position_status = 'BUY_COMPLETE'
                            peak_price = row["STCK_CLPR"]
                            continue

            # === 2단계: 포지션 미보유 또는 추가매수 가능 시 매수 조건 체크 ===
            can_buy = position_status is None or (position_status == 'BUY_COMPLETE' and buy_count < 2)
            if can_buy and current_capital > 0:
                is_entry = False
                buy_amount = 0  # 초기화

                if position_status is None:
                    matched, signal_reason = self._check_entry_conditions(row, prev_row)
                    if matched:
                       is_entry = True
                       buy_amount = current_capital * buy_ratio
                else: # 2차 매수
                    matched, signal_reason = self._check_second_buy_conditions(row, prev_row)
                    if matched:
                        is_entry = True
                        buy_amount = current_capital

                if is_entry and buy_amount > 0:
                    buy_price = row["STCK_CLPR"]
                    buy_quantity = int(buy_amount / buy_price)

                    if buy_quantity > 0:
                        is_first_buy = position_status is None
                        reason = f"{buy_count+1}차 매수({signal_reason})"
                        current_capital = self._execute_buy(trades, current_date, buy_price, buy_quantity, current_capital, reason)
                        buy_count += 1
                        position_status = 'BUY_COMPLETE'
                        if is_first_buy:
                            peak_price = row["STCK_CLPR"]

        # 최종 청산 및 결과 포맷팅
        final_capital = self._liquidate_final_position(trades, eval_df, current_capital)
        result = self._format_result(prices_df, params, trades, final_capital)
        return result

    def _check_immediate_sell_conditions(self, row: pd.Series) -> Tuple[bool, str, float]:
        """[1차 방어선] 저가 기준 즉시 매도 조건 체크

        Returns:
            (신호발생여부, 사유, 매도가)
        """
        low_price = row["STCK_LWPR"]

        # EMA-ATR 동적 손절 (NaN 체크 + ATR=0 가드)
        if pd.notna(row["ema_20"]) and pd.notna(row["atr"]) and row["atr"] > 0:
            ema_stop_loss = row["ema_20"] - (row["atr"] * self.ATR_MULTIPLIER)
            if low_price <= ema_stop_loss:
                return True, "추세 이탈 손절", ema_stop_loss

        return False, "", 0.0

    def _check_eod_trailing_stop(self, row, prev_row, prev_prev_row, peak_price, position_status) -> Tuple[Optional[str], str]:
        """[2차 방어선] EOD 조건부 trailing stop

        조건: 추세 약화((+DI - -DI) 격차 2일 연속 감소) + 수급 약화(OBV z-score 감소) 충족 시
        → 고점 대비 하락률로 매도 단계 결정
        """
        # NaN 체크
        required_cols = ["minus_di", "plus_di", "obv_z"]
        if any(pd.isna(row[col]) for col in required_cols):
            return None, ""
        if any(pd.isna(prev_row.get(col)) for col in ["minus_di", "plus_di", "obv_z"]):
            return None, ""
        if any(pd.isna(prev_prev_row[col]) for col in ["minus_di", "plus_di"]):
            return None, ""

        # 조건 1: 추세 약화 — (+DI - -DI) 격차 2일 연속 감소
        di_spread_today = row["plus_di"] - row["minus_di"]
        di_spread_prev = prev_row["plus_di"] - prev_row["minus_di"]
        di_spread_prev2 = prev_prev_row["plus_di"] - prev_prev_row["minus_di"]
        trend_weakening = di_spread_today < di_spread_prev < di_spread_prev2

        # 조건 2: 수급 약화 — OBV z-score 감소 (전일 대비)
        supply_weakening = row["obv_z"] < prev_row["obv_z"]
        if not (trend_weakening and supply_weakening):
            return None, ""

        # 고점 대비 하락률 계산 (종가 기준 — 실전과 동일)
        if peak_price <= 0:
            return None, ""

        drawdown_pct = (peak_price - row["STCK_CLPR"]) / peak_price * 100

        # ATR 기반 동적 trailing stop 임계값 계산
        atr = row.get("atr", 0)
        if pd.notna(atr) and atr > 0 and peak_price > 0:
            atr_pct = (atr / peak_price) * 100
            trailing_partial = max(atr_pct * self.TRAILING_STOP_ATR_PARTIAL_MULT, self.TRAILING_STOP_PARTIAL_MIN)
            trailing_full = max(atr_pct * self.TRAILING_STOP_ATR_FULL_MULT, self.TRAILING_STOP_FULL_MIN)
        else:
            trailing_partial = self.TRAILING_STOP_PARTIAL
            trailing_full = self.TRAILING_STOP_FULL

        # 약화 사유 동적 생성
        weakness_reasons = []
        if trend_weakening:
            weakness_reasons.append("추세약화")
        if supply_weakening:
            weakness_reasons.append("수급약화")
        weakness_str = "+".join(weakness_reasons)

        # 2차 전량 매도: SELL_PRIMARY 상태 + ATR 기반 임계값 이상 하락
        if position_status == 'SELL_PRIMARY' and drawdown_pct >= trailing_full:
            return "SELL_ALL", f"2차 전량매도({weakness_str}, 고점대비 -{drawdown_pct:.1f}% 하락, 변동성 기준 -{trailing_full:.1f}% 초과)"

        # 1차 분할 매도: BUY_COMPLETE 상태 + ATR 기반 임계값 이상 하락
        if position_status == 'BUY_COMPLETE' and drawdown_pct >= trailing_partial:
            return "SELL_PRIMARY", f"1차 분할매도({weakness_str}, 고점대비 -{drawdown_pct:.1f}% 하락, 변동성 기준 -{trailing_partial:.1f}% 초과)"

        return None, ""

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

    def _check_entry_conditions(self, row: pd.Series, prev_row: pd.Series = None) -> Tuple[bool, str]:
        """1차 매수 진입: 시나리오 A(눌림목 매집) + 시나리오 B(추세 추종 돌파)"""
        required_cols = ["ema_20", "obv_z", "plus_di", "minus_di", "daily_return", "adx", "atr"]
        if any(pd.isna(row[col]) for col in required_cols):
            return False, ""

        if prev_row is None:
            return False, ""

        current_price = row["STCK_CLPR"]

        # === 공통 필터 ===
        surge_filtered = abs(row["daily_return"]) <= self.MAX_SURGE_RATIO
        if not surge_filtered:
            return False, ""

        prev_day_bullish = prev_row["STCK_CLPR"] >= prev_row["STCK_OPRC"]
        if not prev_day_bullish:
            return False, ""

        # === 시나리오 A: 눌림목 매집 진입 ===
        accum_lower = row["ema_20"] + (row["atr"] * self.ACCUM_ENTRY_ATR_LOWER)
        accum_upper = row["ema_20"] + (row["atr"] * self.ACCUM_ENTRY_ATR_UPPER)


        if accum_lower <= current_price <= accum_upper:
            obv_accumulating = (row["obv_z"] > self.ACCUM_ENTRY_OBV_MIN) and (row["obv_z"] > prev_row["obv_z"])
            adx_mid_range = self.ACCUM_ENTRY_ADX_MIN <= row["adx"] <= self.ACCUM_ENTRY_ADX_MAX
            ema_rising = row["ema_20"] > prev_row["ema_20"]
            trend_direction = row["plus_di"] > row["minus_di"]

            if obv_accumulating and adx_mid_range and ema_rising and trend_direction:
                return True, "눌림목매집(EMA근접+OBV양호+추세상승)"

        # === 시나리오 B: 추세 추종 EMA 돌파 진입 ===
        ema_rising = row["ema_20"] > prev_row["ema_20"]
        price_above_ema = current_price > row["ema_20"]
        within_gap_limit = current_price <= row["ema_20"] * self.BREAKOUT_ENTRY_GAP_MAX

        if ema_rising and price_above_ema and within_gap_limit:
            trend_direction = row["plus_di"] > row["minus_di"]
            adx_sufficient = row["adx"] > self.BREAKOUT_ENTRY_ADX_MIN  # 최소 추세 강도
            obv_positive = row["obv_z"] > self.BREAKOUT_ENTRY_OBV_MIN

            if trend_direction and adx_sufficient and obv_positive:
                return True, "EMA돌파(상향돌파+추세확인+거래량동반)"

        return False, ""

    def _check_second_buy_conditions(self, row: pd.Series, prev_row: pd.Series = None) -> Tuple[bool, str]:
        """2차 매수: 추세 안정화 확인 후 추가 매수 (단일 조건)"""
        if any(pd.isna(row[col]) for col in ["ema_20", "obv_z", "atr", "adx", "plus_di", "minus_di"]):
            return False, ""

        if prev_row is None:
            return False, ""

        # 전일 양봉 필터
        if not (prev_row["STCK_CLPR"] >= prev_row["STCK_OPRC"]):
            return False, ""

        current_price = row["STCK_CLPR"]

        # 가격 위치: EMA 이상 ~ EMA + ATR × 2.0 (추세 확인 + 과열 방지)
        lower = row["ema_20"] + (row["atr"] * self.SECOND_BUY_ATR_LOWER)
        upper = row["ema_20"] + (row["atr"] * self.SECOND_BUY_ATR_UPPER)

        if lower <= current_price <= upper:
            # 추세 안정: ADX >= 20 + 상승 방향
            if row["adx"] >= self.SECOND_BUY_ADX_MIN and row["plus_di"] > row["minus_di"]:
                # 수급 확인: OBV z-score
                if row["obv_z"] >= self.SECOND_BUY_OBV_MIN:
                    return True, "추세안정(EMA상단+ADX확인+OBV양전)"

        return False, ""
        
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
