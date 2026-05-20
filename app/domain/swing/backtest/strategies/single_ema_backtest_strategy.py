"""
단일 20EMA 백테스팅 전략 (Single EMA Backtest Strategy) — 2단계 분할 익절

**매수:** 시나리오 A(눌림목) + B(돌파), Conviction 기반 포지션 사이징
**손절:** EMA - ATR×2.0 (SIGNAL 2에서는 본전 방어)
**1차 익절:** 고점 - ATR×2.0 → 50% 매도
**2차 익절:** 고점 - ATR×2.0 AND OBV z-score < -0.5 → 잔량 전량 매도
"""
import pandas as pd
from typing import Dict, List, Optional, Tuple
from .base_strategy import BacktestStrategy, ceil_tick, floor_tick
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
        signal = 0          # 0:대기, 1:보유-익절전, 2:보유-익절후, 3:수급이탈대기, 4:수급재유입대기
        peak_price = 0.0
        entry_price = 0.0   # 본전 방어용
        stop_loss = 0.0     # 고정 손절가 (매수 시 확정)
        hold_qty = 0

        for i in range(2, len(eval_df)):
            row = eval_df.iloc[i]
            prev_row = eval_df.iloc[i-1]
            current_date = row["STCK_BSOP_DATE"]

            # === 1단계: 포지션 보유 시 매도 조건 체크 ===
            if signal in (1, 2):
                # PEAK 갱신 (매도 체크 전에 고가 반영)
                peak_price = max(peak_price, row["STCK_HGPR"])

                # [손절] 고정 손절 (entry - ATR×2.0, SIGNAL 2에서는 본전 방어)
                if stop_loss > 0:
                    current_stop = max(stop_loss, entry_price) if signal == 2 else stop_loss

                    if row["STCK_LWPR"] <= current_stop:
                        reason = "손절" if signal == 1 else "손절(본전방어)"
                        sell_price = floor_tick(min(current_stop, row["STCK_OPRC"]))
                        current_capital = self._execute_sell(
                            trades, current_date, sell_price, current_capital,
                            [reason, f"손절가 {sell_price:.0f}원"]
                        )
                        signal = 3
                        peak_price, entry_price, stop_loss, hold_qty = 0.0, 0.0, 0.0, 0
                        continue

                # [Trailing Stop 익절]
                if peak_price > 0 and pd.notna(row["atr"]) and row["atr"] > 0:
                    stop_price = floor_tick(peak_price - row["atr"] * self.TRAILING_STOP_ATR_MULT)

                    if row["STCK_LWPR"] <= stop_price:
                        trailing_sell_price = floor_tick(min(stop_price, row["STCK_OPRC"]))

                        # 실제 체결가가 매수가 미만이면 익절 스킵 (손절 라인에서 처리)
                        if trailing_sell_price < entry_price:
                            continue

                        drawdown_pct = round((peak_price - row["STCK_LWPR"]) / peak_price * 100, 1)

                        if signal == 1:
                            # 1차 익절: 50% 매도 (수익일 때만, 갭하락 시 시가로 체결)
                            sell_qty = hold_qty // 2
                            if sell_qty > 0:
                                current_capital = self._execute_partial_sell(
                                    trades, current_date, trailing_sell_price, sell_qty,
                                    current_capital,
                                    ["1차익절", f"고점대비 -{drawdown_pct}%"]
                                )
                                hold_qty -= sell_qty
                                signal = 2
                                peak_price = trailing_sell_price  # PEAK 리셋
                                continue

                        elif signal == 2:
                            # 2차 익절: OBV 게이트 확인 (갭하락 시 시가로 체결)
                            obv_z_sell = row.get("obv_z_sell", 0)
                            if pd.notna(obv_z_sell) and obv_z_sell < self.OBV_Z_SELL_THRESHOLD:
                                current_capital = self._execute_sell(
                                    trades, current_date, trailing_sell_price,
                                    current_capital,
                                    ["2차익절", f"고점대비 -{drawdown_pct}%", f"OBV z14={obv_z_sell:.2f}"]
                                )
                                signal = 3
                                peak_price, entry_price, stop_loss, hold_qty = 0.0, 0.0, 0.0, 0
                                continue


            # === 2단계: 수급 안정화 대기 (SIGNAL 3) ===
            # OBV z ≤ 0 한번 찍은 뒤(signal 4), 다시 양수 전환해야 매수 가능
            if signal == 3:
                obv_z = row.get("obv_z", 0)
                if pd.notna(obv_z) and obv_z < self.COOLDOWN_OBV_EXIT:
                    signal = 4  # 확실한 수급 이탈 확인, 재유입 대기
                continue

            if signal == 4:
                obv_z = row.get("obv_z", 0)
                if pd.notna(obv_z) and obv_z > self.COOLDOWN_OBV_REENTRY:
                    signal = 0  # 확실한 수급 재유입 확인, 매수 가능
                continue

            # === 3단계: 포지션 미보유 시 매수 (Conviction 기반 포지션 사이징) ===
            if signal == 0 and current_capital > 0:
                matched, signal_reasons, signal_price = self._check_entry_conditions(row, prev_row)

                if matched:
                    # 신호 가격이 당일 [저가, 고가] 범위 안이면 해당 가격으로 체결 (호가 올림)
                    if signal_price > 0 and row["STCK_LWPR"] <= signal_price <= row["STCK_HGPR"]:
                        buy_price = ceil_tick(signal_price)
                    else:
                        buy_price = row["STCK_CLPR"]
                    atr = row.get("atr", 0)

                    # Conviction 기반 포지션 사이징
                    adx_val = row.get("adx", 0) if pd.notna(row.get("adx", 0)) else 0
                    obv_z_val = row.get("obv_z", 0) if pd.notna(row.get("obv_z", 0)) else 0
                    conviction = self.calc_conviction(adx_val, obv_z_val)

                    buy_quantity = int(current_capital * self.MAX_ENTRY_PCT * conviction / buy_price)

                    if buy_quantity > 0:
                        reasons = ["매수"] + signal_reasons + [f"conviction={conviction:.2f}"]
                        current_capital = self._execute_buy(trades, current_date, buy_price, buy_quantity, current_capital, reasons)
                        signal = 1
                        peak_price = buy_price
                        entry_price = buy_price
                        stop_loss = floor_tick(buy_price - atr * self.ATR_MULTIPLIER) if pd.notna(atr) and atr > 0 else 0.0
                        hold_qty = buy_quantity

        # 최종 청산 및 결과 포맷팅
        final_capital = self._liquidate_final_position(trades, eval_df, current_capital)
        result = self._format_result(prices_df, params, trades, final_capital, eval_df)
        return result

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """지표 계산 (공통 메서드 사용)"""
        return TechnicalIndicators.prepare_full_indicators_for_single_ema(
            df,
            ema_short=self.EMA_PERIOD,
            ema_long=120,
            atr_period=14,
            adx_period=14,
            obv_lookback=self.OBV_LOOKBACK,
            obv_lookback_sell=self.OBV_LOOKBACK_SELL
        )

    def _check_entry_conditions(self, row: pd.Series, prev_row: pd.Series = None) -> Tuple[bool, List[str], float]:
        """1차 매수 진입: 시나리오 A(눌림목 매집) + 시나리오 B(추세 추종 돌파)"""
        required_cols = ["ema20", "obv_z", "plus_di", "minus_di", "daily_return", "adx", "atr"]
        if any(pd.isna(row[col]) for col in required_cols):
            return False, [], 0.0

        if prev_row is None:
            return False, [], 0.0

        current_price = row["STCK_CLPR"]

        # === 공통 필터 ===
        surge_filtered = abs(row["daily_return"]) <= self.MAX_SURGE_RATIO
        if not surge_filtered:
            return False, [], 0.0

        # 갭 하락 필터: 당일 시가가 전일 저가 미만이면 매수 차단
        prev_low = prev_row.get("STCK_LWPR", 0)
        current_open = row.get("STCK_OPRC", 0)
        if prev_low > 0 and current_open < prev_low:
            return False, [], 0.0

        # 전일 윗꼬리 긴 캔들 필터 (양봉/음봉 무관, 매도 압력 강한 날 다음 매수 차단)
        prev_open = prev_row.get("STCK_OPRC", 0)
        prev_close = prev_row.get("STCK_CLPR", 0)
        prev_high = prev_row.get("STCK_HGPR", 0)
        prev_low = prev_row.get("STCK_LWPR", 0)
        candle_range = prev_high - prev_low
        if candle_range > 0 and prev_close > 0 and candle_range / prev_close > self.MIN_CANDLE_RANGE_PCT:
            upper_shadow = prev_high - max(prev_open, prev_close)
            upper_shadow_ratio = upper_shadow / candle_range
            if upper_shadow_ratio >= self.UPPER_SHADOW_RATIO_MAX:
                return False, [], 0.0

        # === 시나리오 A: 눌림목 매집 진입 ===
        accum_lower = row["ema20"] + (row["atr"] * self.ACCUM_ENTRY_ATR_LOWER)
        accum_upper = row["ema20"] + (row["atr"] * self.ACCUM_ENTRY_ATR_UPPER)


        if accum_lower <= current_price <= accum_upper:
            obv_accumulating = (row["obv_z"] > self.ACCUM_ENTRY_OBV_MIN) and (prev_row["obv_z"] is not None and row["obv_z"] > prev_row["obv_z"])
            adx_mid_range = self.ACCUM_ENTRY_ADX_MIN <= row["adx"] <= self.ACCUM_ENTRY_ADX_MAX
            trend_direction = row["plus_di"] > row["minus_di"]

            if obv_accumulating and adx_mid_range and trend_direction:
                signal_price = row["ema20"]
                return True, ["눌림목매집", "EMA근접", "OBV양호", "추세상승"], signal_price

        # === 시나리오 B: 추세 추종 EMA 돌파 진입 ===
        price_above_ema = current_price > row["ema20"]
        within_gap_limit = current_price <= row["ema20"] * self.BREAKOUT_ENTRY_GAP_MAX

        if price_above_ema and within_gap_limit:
            trend_direction = row["plus_di"] > row["minus_di"]
            adx_sufficient = row["adx"] > self.BREAKOUT_ENTRY_ADX_MIN  # 최소 추세 강도
            obv_positive = row["obv_z"] > self.BREAKOUT_ENTRY_OBV_MIN

            if trend_direction and adx_sufficient and obv_positive:
                signal_price = row["ema20"]
                return True, ["EMA돌파", "상향돌파", "추세확인", "거래량동반"], signal_price

        return False, [], 0.0

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

    def _execute_partial_sell(self, trades: List[Dict], date, price: float, sell_quantity: int,
                             current_capital: float, reasons: List[str]):
        """부분 매도 (1차 익절 50%) 실행"""
        curr_qty, curr_avg_cost = self._calculate_position_state(trades)
        if sell_quantity <= 0 or curr_qty <= 0:
            return current_capital

        sell_amount = sell_quantity * price
        commission = sell_amount * self.COMMISSION_RATE
        tax = sell_amount * self.TAX_RATE
        net_proceeds = sell_amount - commission - tax
        realized_pnl = (price - curr_avg_cost) * sell_quantity - commission - tax
        realized_pnl_pct = ((price / curr_avg_cost) - 1) * 100 if curr_avg_cost > 0 else 0.0

        trades.append({
            'date': date, 'action': 'SELL', 'quantity': sell_quantity,
            'price': float(price), 'amount': float(sell_amount),
            'commission': float(commission), 'tax': float(tax),
            'net_proceeds': float(net_proceeds),
            'current_capital': float(current_capital + net_proceeds),
            'realized_pnl': float(realized_pnl),
            'realized_pnl_pct': round(realized_pnl_pct, 2),
            'reasons': reasons
        })
        return current_capital + net_proceeds

    def _execute_sell(self, trades: List[Dict], date, price: float, current_capital: float, reasons: List[str],
                      sell_all: bool = False):
        """전량 매도 실행"""
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
