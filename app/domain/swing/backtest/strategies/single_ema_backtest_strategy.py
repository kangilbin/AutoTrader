"""
단일 20EMA 백테스팅 전략 (Single EMA Backtest Strategy) — 2단계 분할 익절

**매수:** 시나리오 A(눌림목) + B(돌파), 리스크 기반 포지션 사이징
**손절:** EMA - ATR×1.0 (SIGNAL 2에서는 본전 방어)
**1차 익절:** 고점 - ATR×2.0 → 50% 매도
**2차 익절:** 고점 - ATR×2.0 AND OBV z-score < -0.5 → 잔량 전량 매도
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
        signal = 0          # 0:대기, 1:보유-익절전, 2:보유-익절후
        peak_price = 0.0
        entry_price = 0.0   # 본전 방어용
        hold_qty = 0

        for i in range(2, len(eval_df)):
            row = eval_df.iloc[i]
            prev_row = eval_df.iloc[i-1]
            prev_prev_row = eval_df.iloc[i-2]
            current_date = row["STCK_BSOP_DATE"]

            # === 1단계: 포지션 보유 시 매도 조건 체크 ===
            if signal in (1, 2):
                peak_price = max(peak_price, row["STCK_HGPR"])

                # [손절] EMA-ATR (SIGNAL 2에서는 본전 방어)
                if pd.notna(row["ema_20"]) and pd.notna(row["atr"]) and row["atr"] > 0:
                    ema_atr_stop = row["ema_20"] - (row["atr"] * self.ATR_MULTIPLIER)
                    if signal == 2 and entry_price > 0:
                        ema_atr_stop = max(ema_atr_stop, entry_price)

                    if row["STCK_LWPR"] <= ema_atr_stop:
                        reason = "손절" if signal == 1 else "손절(본전방어)"
                        current_capital = self._execute_sell(
                            trades, current_date, ema_atr_stop, current_capital,
                            [reason, f"손절가 {ema_atr_stop:.0f}원"]
                        )
                        signal, peak_price, entry_price, hold_qty = 0, 0.0, 0.0, 0
                        continue

                # [Trailing Stop 익절]
                if peak_price > 0 and pd.notna(row["atr"]) and row["atr"] > 0:
                    stop_price = peak_price - row["atr"] * self.TRAILING_STOP_ATR_MULT

                    if row["STCK_LWPR"] <= stop_price:
                        drawdown_pct = round((peak_price - row["STCK_LWPR"]) / peak_price * 100, 1)

                        if signal == 1:
                            # 1차 익절: 50% 매도
                            sell_qty = hold_qty // 2
                            if sell_qty > 0:
                                current_capital = self._execute_partial_sell(
                                    trades, current_date, row["STCK_CLPR"], sell_qty,
                                    current_capital,
                                    ["1차익절", f"고점대비 -{drawdown_pct}%"]
                                )
                                hold_qty -= sell_qty
                                signal = 2
                                peak_price = row["STCK_CLPR"]  # PEAK 리셋
                                continue

                        elif signal == 2:
                            # 2차 익절: OBV 게이트 확인
                            obv_z = row.get("obv_z", 0)
                            if pd.notna(obv_z) and obv_z < self.OBV_Z_SELL_THRESHOLD:
                                current_capital = self._execute_sell(
                                    trades, current_date, row["STCK_CLPR"],
                                    current_capital,
                                    ["2차익절", f"고점대비 -{drawdown_pct}%", f"OBV z={obv_z:.2f}"]
                                )
                                signal, peak_price, entry_price, hold_qty = 0, 0.0, 0.0, 0
                                continue

            # === 2단계: 포지션 미보유 시 매수 (리스크 기반 포지션 사이징) ===
            if signal == 0 and current_capital > 0:
                matched, signal_reasons = self._check_entry_conditions(row, prev_row, prev_prev_row)

                if matched:
                    buy_price = row["STCK_CLPR"]
                    atr = row.get("atr", 0)
                    ema = row.get("ema_20", 0)

                    # Qty = min(배정금 × ENTRY_PCT / 현재가, 손실제한 수량)
                    entry_qty = int(current_capital * self.ENTRY_PCT / buy_price)

                    if pd.notna(atr) and atr > 0 and pd.notna(ema) and buy_price > 0:
                        stop_price = ema - atr * self.ATR_MULTIPLIER
                        risk_per_share = buy_price - stop_price
                        if risk_per_share > 0:
                            loss_limit_qty = int(current_capital * self.MAX_LOSS_PCT / risk_per_share)
                            buy_quantity = min(entry_qty, loss_limit_qty)
                        else:
                            buy_quantity = entry_qty
                    else:
                        buy_quantity = entry_qty

                    if buy_quantity > 0:
                        reasons = ["매수"] + signal_reasons
                        current_capital = self._execute_buy(trades, current_date, buy_price, buy_quantity, current_capital, reasons)
                        signal = 1
                        peak_price = buy_price
                        entry_price = buy_price
                        hold_qty = buy_quantity

        # 최종 청산 및 결과 포맷팅
        final_capital = self._liquidate_final_position(trades, eval_df, current_capital)
        result = self._format_result(prices_df, params, trades, final_capital)
        return result

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
