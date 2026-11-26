"""매매 이력 차트(GET /trade-history/{swing_id}) — 전략별 오버레이: 단일 EMA 는 EMA20, 일목은 일목 선"""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app.domain.trade_history.schemas import TradeHistoryWithChartResponse
from app.domain.trade_history.service import TradeHistoryService


def _rows(n=700, seed=2, end="2026-10-30"):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0008, 0.015, n)))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    days = pd.bdate_range(end=end, periods=n + 5)[-n:].strftime("%Y%m%d")
    return [{"STCK_BSOP_DATE": d, "STCK_OPRC": float(o), "STCK_HGPR": float(max(o, c) * 1.004),
             "STCK_LWPR": float(min(o, c) * 0.996), "STCK_CLPR": float(c), "ACML_VOL": 1_000_000}
            for d, o, c in zip(days, open_, close)]


class FakeRepo:
    def __init__(self, swing_type):
        self.swing = SimpleNamespace(SWING_ID=5, ST_CODE="AAA", MRKT_CODE="NAS", SWING_TYPE=swing_type)

    async def find_swing_with_ownership(self, swing_id, user_id):
        return self.swing

    async def find_by_swing_id_and_period(self, swing_id, start, end):
        return []


async def chart(swing_type, start=date(2026, 1, 1), end=date(2026, 10, 30)):
    captured = {}
    rows = _rows()

    class FakeStock:
        def __init__(self, db):
            pass

        async def get_stock_history(self, mrkt, code, since):
            captured["since"] = since
            return rows

    service = TradeHistoryService(None)
    service.repo = FakeRepo(swing_type)
    with patch("app.domain.stock.service.StockService", FakeStock):
        result = await service.get_trade_history_with_chart("u1", 5, start, end)
    return result, captured, rows


@pytest.mark.asyncio
async def test_single_ema_swing_chart_is_unchanged_and_has_no_ichimoku():
    result, captured, _ = await chart("S")
    assert result["swing_type"] == "S"
    assert result["ema20_history"] and any(x["ema20"] is not None for x in result["ema20_history"])
    assert result["ichimoku_history"] == [] and result["ichimoku_future"] == []
    assert captured["since"] == datetime(2025, 11, 1)                          # EMA20 워밍업은 기존처럼 2개월


@pytest.mark.asyncio
async def test_ichimoku_swing_chart_has_ichimoku_lines_and_no_ema():
    result, captured, rows = await chart("I")
    assert result["swing_type"] == "I"
    assert result["ema20_history"] == []                                        # 앱이 EMA20 을 겹쳐 그리지 않는다
    hist = result["ichimoku_history"]
    assert [h["STCK_BSOP_DATE"] for h in hist] == [p["STCK_BSOP_DATE"] for p in result["price_history"]]   # 날짜축이 캔들과 같다
    assert hist[0]["STCK_BSOP_DATE"] >= "20260101"
    assert all(h["senkou_a"] is not None and h["tenkan"] is not None for h in hist)       # 9개월 워밍업이면 기간 첫날부터 구름이 있다
    assert len(result["ichimoku_future"]) == 26 and result["ichimoku_future"][0]["STCK_BSOP_DATE"] > hist[-1]["STCK_BSOP_DATE"]
    assert captured["since"] == datetime(2025, 4, 1)                            # 일목은 구름 워밍업 9개월


@pytest.mark.asyncio
async def test_ichimoku_chart_for_past_period_has_no_future_cloud():
    result, _, _ = await chart("I", start=date(2026, 1, 1), end=date(2026, 6, 30))
    assert result["ichimoku_history"] and result["ichimoku_future"] == []


@pytest.mark.asyncio
async def test_response_matches_schema_for_both_types():
    for t in ("S", "I"):
        result, _, _ = await chart(t)
        result = {**result, "start_date": str(result["start_date"]), "end_date": str(result["end_date"])}
        parsed = TradeHistoryWithChartResponse(**result)
        assert parsed.swing_type == t
