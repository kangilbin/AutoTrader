"""3년치 일봉 적재(fetch_and_store_3_years_data) 회귀 테스트

실행:
  uv run pytest tests/test_stock_history_loader.py -v

문제: 날짜 구간을 동시에(세마포어 3) 조회하면서 DB 세션 하나를 같이 써서 일부 구간이
"concurrent operations are not permitted"로 빠졌다. 빠진 구간도 '데이터 없음'과 똑같이
None으로 처리돼 DATA_YN='Y'(완료)로 닫혔고, 3년치에 몇 달짜리 구멍이 영구히 남았다.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.stock import stock_data_batch


class _FakeStockService:
    """update_stock·save_history_bulk 호출만 기록"""
    updates: list = []
    saved: list = []

    def __init__(self, db):
        pass

    async def update_stock(self, mrkt_code, st_code, data):
        _FakeStockService.updates.append(data["DATA_YN"])

    async def save_history_bulk(self, rows):
        _FakeStockService.saved.extend(rows)
        return len(rows)


def _bar(date: str) -> dict:
    return {"STCK_BSOP_DATE": date, "STCK_OPRC": "100", "STCK_HGPR": "101",
            "STCK_LWPR": "99", "STCK_CLPR": "100", "ACML_VOL": "1000"}


class LoaderTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        _FakeStockService.updates, _FakeStockService.saved = [], []
        self.sessions = []

        async def new_session():
            s = MagicMock(close=AsyncMock())
            self.sessions.append(s)
            return s

        self.patches = [
            patch.object(stock_data_batch.Database, "get_session", side_effect=new_session),
            patch.object(stock_data_batch, "StockService", _FakeStockService),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    async def _run(self, fetch):
        with patch.object(stock_data_batch, "get_stock_data", side_effect=fetch):
            await stock_data_batch.fetch_and_store_3_years_data("U", "J", "005930", {})

    async def test_each_range_uses_its_own_session(self):
        used = []

        async def fetch(user_id, code, start, end, db):
            used.append(db)
            return {"output2": [_bar(start)]}

        await self._run(fetch)
        self.assertGreater(len(used), 1)
        self.assertEqual(len(set(map(id, used))), len(used), "구간들이 세션을 공유함")
        outer = self.sessions[0]
        self.assertNotIn(outer, used, "바깥 세션을 구간 조회에 씀")
        for s in used:
            s.close.assert_awaited_once()
        self.assertEqual(_FakeStockService.updates, ["P", "Y"])

    async def test_failed_range_is_not_marked_complete(self):
        calls = {"n": 0}

        async def fetch(user_id, code, start, end, db):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("KIS 조회 실패")
            return {"output2": [_bar(start)]}

        with self.assertRaises(RuntimeError):
            await self._run(fetch)
        self.assertEqual(_FakeStockService.updates, ["P", "E"], "구간 실패인데 완료(Y)로 닫힘")

    async def test_empty_range_is_not_failure(self):
        """상장 전 구간처럼 정상 응답에 데이터가 없으면 실패가 아니다"""
        calls = {"n": 0}

        async def fetch(user_id, code, start, end, db):
            calls["n"] += 1
            return {"output2": []} if calls["n"] == 1 else {"output2": [_bar(start)]}

        await self._run(fetch)
        self.assertEqual(_FakeStockService.updates, ["P", "Y"])

    async def test_years_controls_range(self):
        """기간 인자 — 기본 3년, 검증용으로 더 길게 받을 수 있다"""
        starts = {}

        for years in (3, 6):
            seen = []

            async def fetch(user_id, code, start, end, db):
                seen.append(start)
                return {"output2": [_bar(start)]}

            with patch.object(stock_data_batch, "get_stock_data", side_effect=fetch):
                await stock_data_batch.fetch_and_store_3_years_data("U", "J", "005930", {}, years=years)
            starts[years] = min(seen)

        self.assertEqual(int(starts[3][:4]) - int(starts[6][:4]), 3)


if __name__ == "__main__":
    unittest.main()
