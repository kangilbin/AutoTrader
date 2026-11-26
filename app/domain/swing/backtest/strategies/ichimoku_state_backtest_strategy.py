"""
일목균형표(SWING_TYPE='I') 백테스팅 전략 — 실전 IchimokuStrategy 와 같은 규칙 (BaseIchimokuStrategy)

**진입:** 구름 위 + 전환선 > 기준선 상태가 최근 5거래일 안에 성립해 유지 중 → 다음 봉 시가 매수
**청산:** 종가가 구름 하단 아래로 마감 → 다음 봉 시가 매도 (부분익절·쿨다운 없음)
**수량:** 배정금(init_amount)의 95% (full_entry 면 100%) — 실전은 풀 사이징(손절선=구름 하단 기준 위험 비율)이 정하지만,
          이 백테스트는 종목 하나에 배정금을 거는 단일 종목 시뮬레이션이라 고정 비율로 본다.
차트용으로 price_history 와 ichimoku_history(전환선·기준선·선행스팬 A/B·구름 상/하단·후행스팬) · ichimoku_future(미래 구름 26봉)를 함께 돌려준다.
"""
from typing import Dict

import pandas as pd

from app.domain.swing.trading.strategies.base_ichimoku import BaseIchimokuStrategy
from .base_strategy import BacktestStrategy, fill_buy, fill_sell, records_without_nan, trade_costs


class IchimokuBacktestStrategy(BacktestStrategy, BaseIchimokuStrategy):
    """일목균형표 백테스팅 전략"""

    ENTRY_PCT = 0.95            # 배정금 중 진입에 쓰는 비율 (풀의 현금 유보 5%와 같은 감각)

    def __init__(self):
        super().__init__("일목균형표 전략")

    def min_bars(self, params: dict) -> int:
        """구름 계산 + 최근 상태 판정에 필요한 최소 봉 수 — 이보다 짧으면 신호가 전부 False 라 '거래 0건'이 정상 결과처럼 보인다"""
        return self.MIN_BARS

    def compute(self, prices_df: pd.DataFrame, params: Dict) -> Dict:
        initial_capital = params["init_amount"]
        eval_start = pd.to_datetime(params["eval_start"])
        mrkt_code = params.get("mrkt_code", "")
        entry_pct = 1.0 if params.get("full_entry", False) else self.ENTRY_PCT
        commission = trade_costs(mrkt_code)[0]

        df = self._prepare_data(prices_df)
        frame = self.compute_frame(df)
        dates = df["STCK_BSOP_DATE"]
        trades, capital, holding = [], initial_capital, False

        for i in range(len(df) - 1):                 # t 종가로 판정, t+1 시가로 체결
            next_date = dates.iloc[i + 1]
            if next_date < eval_start:
                continue
            open_next = float(df["STCK_OPRC"].iloc[i + 1])
            if open_next <= 0:
                continue
            if not holding:
                if bool(frame["entry"].iloc[i]) and pd.notna(frame["cloud_bot"].iloc[i]):
                    price = fill_buy(open_next, mrkt_code)
                    qty = int(capital * entry_pct // (price * (1 + commission)))
                    if qty > 0:
                        capital = self._execute_buy(
                            trades, next_date, price, qty, capital,
                            ["일목 진입", f"구름 위 + 전환선>기준선 (최근 {self.RECENT_DAYS}일 내 성립)"], mrkt_code=mrkt_code)
                        holding = True
            elif bool(frame["exit"].iloc[i]):
                close, bot = float(df["STCK_CLPR"].iloc[i]), float(frame["cloud_bot"].iloc[i])
                capital = self._execute_sell(
                    trades, next_date, fill_sell(open_next, mrkt_code), capital,
                    ["구름 하단 이탈", f"종가 {close:,.2f} < 구름 하단 {bot:,.2f}"], mrkt_code=mrkt_code)
                holding = False

        eval_df = df[df["STCK_BSOP_DATE"] >= eval_start].copy()
        final_capital = self._liquidate_final_position(trades, eval_df, capital, mrkt_code=mrkt_code)
        result = self._format_result(prices_df, params, trades, final_capital, eval_df)

        chart = eval_df.copy()
        chart["STCK_BSOP_DATE"] = chart["STCK_BSOP_DATE"].dt.strftime("%Y%m%d")
        result["price_history"] = records_without_nan(
            chart, ["STCK_BSOP_DATE", "STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR", "ACML_VOL"])
        result.update(self.chart_payload(df, start=eval_start))   # ichimoku_history · ichimoku_future (TradingView 용)
        return result
