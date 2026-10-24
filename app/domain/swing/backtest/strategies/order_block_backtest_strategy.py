"""
오더블럭 백테스팅 전략 (Order Block Backtest Strategy)

**매수:** BOS 후 처음 되돌아온 오더블럭 근접선 — 체결가 = min(시가, 근접선)
**손절:** 원격선 − ATR×STOP_ATR_BUFFER (매수 시 확정)
**청산:** 단일 청산선 — 손절가 → +1R 본전 → +2R 고점 − ATR×TRAIL_ATR_MULT
**부분 익절:** 진입가 + 2R 에서 절반 (SIGNAL 1 → 2)
규칙: docs/02-design/features/order-block-strategy.design.md (9장)

선견 방지: 봉 i 판정에 쓰는 OB·ATR은 모두 i−1봉(전일 완성봉)까지의 정보다.
"""
from typing import Dict, List, Optional

import pandas as pd

from .base_strategy import BacktestStrategy, fill_buy, fill_sell, sell_tick
from app.domain.swing.indicators import TechnicalIndicators
from app.domain.swing.order_block import active_order_block, detect_order_blocks
from app.domain.swing.trading.strategies.base_order_block import BaseOrderBlockStrategy
from app.domain.swing.trading.strategies.base_single_ema import BaseSingleEMAStrategy


class OrderBlockBacktestStrategy(BacktestStrategy, BaseOrderBlockStrategy):
    """오더블럭 백테스팅 전략"""

    def __init__(self):
        super().__init__("오더블럭 전략")

    def min_bars(self, params: dict) -> int:
        """장기 추세 EMA 워밍업 + 직전 봉"""
        return self.TREND_EMA_PERIOD + 1

    def compute(self, prices_df: pd.DataFrame, params: Dict) -> Dict:
        initial_capital = params["init_amount"]
        eval_start = pd.to_datetime(params["eval_start"])
        mrkt_code = params.get("mrkt_code", "")   # 호가 단위 (없으면 국내)
        full_entry = params.get("full_entry", False)

        df = self._calculate_indicators(self._prepare_data(prices_df)).reset_index(drop=True)
        ob_params = self.ob_params()
        candidates = detect_order_blocks(df, ob_params)   # 봉 i에선 bos_idx ≤ i−1 인 것만 쓰인다

        eval_rows = df.index[df["STCK_BSOP_DATE"] >= eval_start]
        first = max(int(eval_rows[0]), 1) if len(eval_rows) else len(df)
        eval_df = df.iloc[first:].copy()

        stats = {
            "ob_detected": sum(1 for ob in candidates if ob.bos_idx >= first),
            "touches": 0, "entries": 0, "gap_invalidations": 0,
            "blocked": {"stop_wide": 0, "qty": 0},
            "days_to_1r": [], "exit_stages": {}, "r_multiples": [],
        }

        trades: List[Dict] = []
        capital = initial_capital
        signal = 0          # 0:대기, 1:보유-익절전, 2:보유-익절후, 3:수급이탈대기, 4:수급재유입대기
        entry = stop = peak = 0.0
        hold_qty = 0
        used = set()        # 매수에 쓴 OB (미터치 규칙상 다음 봉부터 소멸하지만 안전장치)
        cycle: Optional[Dict] = None

        for i in range(first, len(df)):
            row, prev = df.iloc[i], df.iloc[i - 1]
            date = row["STCK_BSOP_DATE"]
            atr = float(prev["atr"]) if pd.notna(prev["atr"]) else 0.0   # 전일 완성봉 (실전 캐시와 동일)

            # === 1단계: 보유 중 — 단일 청산선 + 부분 익절 ===
            if signal in (1, 2):
                # PEAK는 전일 고가까지만 (같은 봉 PEAK 갱신 + 청산 동시 발동 방지)
                peak = max(peak, float(prev["STCK_HGPR"]))
                if cycle["days_to_1r"] is None and row["STCK_HGPR"] >= entry + cycle["r"]:
                    cycle["days_to_1r"] = i - cycle["entry_i"]

                exit_line = sell_tick(self.calculate_exit_line(entry, peak, stop, atr), mrkt_code)
                if row["STCK_LWPR"] <= exit_line:
                    px = fill_sell(min(exit_line, row["STCK_OPRC"]), mrkt_code)
                    stage = self.exit_stage(entry, peak, stop)
                    capital = self._execute_sell(
                        trades, date, px, capital,
                        [stage, f"청산선 {px:.2f}", f"손익 {(px - entry) / entry * 100:+.1f}%"], mrkt_code=mrkt_code
                    )
                    self._close_cycle(stats, cycle, px, hold_qty, stage)
                    signal, entry, stop, peak, hold_qty, cycle = 3, 0.0, 0.0, 0.0, 0, None
                    continue

                if signal == 1:
                    target = self.partial_target(entry, stop)
                    sell_qty = int(hold_qty * self.FIRST_PROFIT_TAKE_RATIO)
                    if row["STCK_HGPR"] >= target and sell_qty > 0:
                        px = fill_sell(max(target, row["STCK_OPRC"]), mrkt_code)
                        capital = self._execute_partial_sell(
                            trades, date, px, sell_qty, capital,
                            [f"부분익절(+{self.PARTIAL_TP_R:g}R)", f"목표가 {px:.2f}"], mrkt_code=mrkt_code
                        )
                        cycle["exit_value"] += px * sell_qty
                        hold_qty -= sell_qty
                        signal = 2      # PEAK는 유지 (잔량도 같은 청산선이 지킨다)
                continue

            # === 2단계: 수급 안정화 대기 (단일 이평선과 동일) ===
            if signal == 3:
                obv_z = row.get("obv_z", 0)
                if pd.notna(obv_z) and obv_z < self.COOLDOWN_OBV_EXIT:
                    signal = 4
                continue
            if signal == 4:
                obv_z = row.get("obv_z", 0)
                if pd.notna(obv_z) and obv_z > self.COOLDOWN_OBV_REENTRY:
                    signal = 0
                continue

            # === 3단계: 매수 대기 — 전일까지 확정된 활성 OB의 첫 리테스트 ===
            if capital <= 0:
                continue
            ob = active_order_block(df, candidates, i - 1, ob_params)
            if ob is None or ob.ob_idx in used:
                continue
            if row["STCK_OPRC"] < ob.distal:
                stats["gap_invalidations"] += 1    # 시가가 존 아래 → 갭 붕괴
                continue
            if row["STCK_LWPR"] > ob.proximal:
                continue                            # 아직 존까지 안 내려옴
            stats["touches"] += 1

            # 실전의 추격 제한(+ATR×ENTRY_CHASE_ATR)은 현재가가 없어 재현 불가 → 체결가를 근접선 이하로 강제
            buy_price = fill_buy(min(row["STCK_OPRC"], ob.proximal), mrkt_code)
            stop_px = self.stop_price(ob.distal, atr)
            if atr <= 0 or not self.stop_pct_ok(buy_price, stop_px):
                stats["blocked"]["stop_wide"] += 1
                continue
            qty = self.entry_qty(capital, buy_price, stop_px, full_entry)
            if qty <= 0:
                stats["blocked"]["qty"] += 1
                continue

            sizing = "전량 매수" if full_entry else f"위험 {self.RISK_PER_TRADE:.0%}"
            capital = self._execute_buy(
                trades, date, buy_price, qty, capital,
                ["매수", "오더블럭", f"존 {ob.distal:.2f}~{ob.proximal:.2f}", f"손절 {stop_px:.2f}", sizing], mrkt_code=mrkt_code
            )
            stats["entries"] += 1
            used.add(ob.ob_idx)
            signal, entry, stop, peak, hold_qty = 1, buy_price, stop_px, buy_price, qty
            cycle = {"entry_i": i, "entry": entry, "r": entry - stop, "qty": qty,
                     "exit_value": 0.0, "days_to_1r": None}

            # 진입봉 손절 — 일봉은 장중 순서를 모르므로 저가가 손절가에 닿았으면 손절로 본다 (보수적)
            # 같은 봉 부분익절은 판정하지 않는다 (같은 이유)
            if row["STCK_LWPR"] <= stop_px:
                px = fill_sell(stop_px, mrkt_code)
                capital = self._execute_sell(
                    trades, date, px, capital,
                    ["손절", "진입봉", f"손익 {(px - entry) / entry * 100:+.1f}%"], mrkt_code=mrkt_code
                )
                self._close_cycle(stats, cycle, px, hold_qty, "손절")
                signal, entry, stop, peak, hold_qty, cycle = 3, 0.0, 0.0, 0.0, 0, None
            elif row["STCK_HGPR"] >= entry + cycle["r"]:
                cycle["days_to_1r"] = 0

        # 평가 종료 시 보유분은 마지막 종가로 청산 (통계에도 반영)
        if hold_qty > 0 and cycle:
            self._close_cycle(stats, cycle, float(df.iloc[-1]["STCK_CLPR"]), hold_qty, "최종 청산")
        final_capital = self._liquidate_final_position(trades, eval_df, capital, mrkt_code=mrkt_code)

        result = self._format_result(prices_df, params, trades, final_capital, eval_df)
        result["strategy_stats"] = self._summarize_stats(stats, mrkt_code)
        return result

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """지표 계산 — atr·avg_vol20·ema120(장기 추세)·obv_z(쿨다운)·ema20(차트)

        장기 EMA 컬럼명은 기간과 무관하게 ema120 이다 (prepare_indicators_from_df 규약).
        """
        return TechnicalIndicators.prepare_indicators_from_df(
            df,
            ema_long=self.TREND_EMA_PERIOD,
            obv_lookback=BaseSingleEMAStrategy.OBV_LOOKBACK,
            accum_ema_period=BaseSingleEMAStrategy.ACCUM_EMA_PERIOD,
        )

    @staticmethod
    def _close_cycle(stats: Dict, cycle: Dict, px: float, qty: int, stage: str) -> None:
        """사이클 종료 통계 — R 배수는 부분익절 포함 평균 청산가 기준 (비용 제외)"""
        avg_exit = (cycle["exit_value"] + px * qty) / cycle["qty"]
        stats["r_multiples"].append((avg_exit - cycle["entry"]) / cycle["r"])
        stats["days_to_1r"].append(cycle["days_to_1r"])
        stats["exit_stages"][stage] = stats["exit_stages"].get(stage, 0) + 1

    def _summarize_stats(self, stats: Dict, mrkt_code: str) -> Dict:
        r_multiples = stats.pop("r_multiples")
        stats["avg_r_multiple"] = round(sum(r_multiples) / len(r_multiples), 2) if r_multiples else None
        stats["proximal_source"] = self.PROXIMAL_SOURCE
        stats["mrkt_code"] = mrkt_code
        return stats
