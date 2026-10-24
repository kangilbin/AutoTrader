"""장중 5분 샘플 저장 테스트

실행:
  uv run pytest tests/test_intraday_sample.py -v

매매 배치가 사이클마다 받은 시세를 STOCK_INTRADAY_SAMPLE에 남기는지, 저장이 실패해도
매매가 그대로 진행되는지 확인한다 (test_swing_trade_scenarios 가짜 인프라 재사용).
"""
from unittest.mock import patch

from app.domain.swing.trading import auto_swing_batch as batch
from tests.test_swing_trade_scenarios import Market, SwingScenarioBase


class _Capture:
    samples = []
    fail = False

    def __init__(self, db):
        pass

    async def save_intraday_sample(self, sample):
        if _Capture.fail:
            raise RuntimeError("DB 장애")
        _Capture.samples.append(sample)


class IntradaySampleTest(SwingScenarioBase):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        _Capture.samples, _Capture.fail = [], False
        p = patch.object(batch, "StockService", _Capture)
        p.start()
        self.addCleanup(p.stop)

    async def test_each_cycle_stores_quote(self):
        await self.run_cycles(2)
        self.assertEqual(len(_Capture.samples), 2)
        s = _Capture.samples[-1]
        self.assertEqual((s["MRKT_CODE"], s["ST_CODE"]), ("NAS", "AAPL"))
        self.assertEqual(float(s["CUR_PRICE"]), Market.price)
        self.assertEqual(s["SAMPLE_DT"].second, 0, "분 단위로 절사되지 않음")

    async def test_store_failure_does_not_block_trading(self):
        _Capture.fail = True
        await self.enter_position()                 # 저장이 계속 실패해도 매수까지 진행
        self.assertEqual(len(Market.buys()), 1)
