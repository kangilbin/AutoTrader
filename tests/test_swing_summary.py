"""
스윙 목록·요약(mapping_swing) 테스트

실행:
  uv run pytest tests/test_swing_summary.py -v

앞부분(LegacySummaryTest)은 풀을 쓰지 않는 계좌의 기존 표시를 숫자로 고정한다 — 풀 모드 처리를 넣은 뒤에도
그대로여야 한다. 뒷부분은 풀 모드(풀 설정 켜짐)의 표시를 확인한다:
- 매도 후 행에도 확정 손익이 남는다 (예전엔 0 으로 표시되어 손절 손실이 요약에서 사라졌다)
- 요약 수익률은 총자산 기준 (누적 손익 ÷ 현재 총평가)
"""
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.domain.pool.entity import PoolSetting
from app.domain.swing import service as svc_mod
from app.domain.swing.service import SwingService

ACCOUNT = "12345678-01"
D = Decimal


class FakeDB:
    async def commit(self):
        pass

    async def rollback(self):
        pass


def row(swing_id, code, mrkt="J"):
    return {"SWING_ID": swing_id, "ACCOUNT_NO": ACCOUNT, "MRKT_CODE": mrkt, "ST_CODE": code, "ST_NM": code,
            "USE_YN": "Y", "SIGNAL": 0, "HOLD_QTY": 0}


def holding(code, qty, evlu, pfls, rate, pchs):
    return {"pdno": code, "prdt_name": code, "hldg_qty": str(qty), "evlu_amt": str(evlu),
            "evlu_pfls_amt": str(pfls), "evlu_pfls_rt": str(rate), "pchs_amt": str(pchs), "prpr": "70000"}


# 총평가 1,000만 원 = 예수금 930만 + 보유평가 70만
OUTPUT2 = {"tot_evlu_amt": "10000000", "evlu_pfls_smtl_amt": "50000", "dnca_tot_amt": "9300000"}


class Env:
    def __init__(self, swings, holdings, pool_setting=None, realized=None, pool_error=None, output2=None):
        self.swings, self.holdings, self.output2 = swings, holdings, output2 or OUTPUT2
        self.pool_setting, self.realized, self.pool_error = pool_setting, realized or {}, pool_error
        self.sum_calls = []

    async def run(self, mrkt="J"):
        service = SwingService(FakeDB())
        service.repo = type("R", (), {"find_all_by_account_no": staticmethod(
            lambda account_no, mrkt_code=None: self._coro(self.swings))})()
        env = self

        class FakePoolRepo:
            def __init__(self, db):
                pass

            async def find(self, account_no, market):
                if env.pool_error:
                    raise env.pool_error
                return env.pool_setting

        class FakeHistoryRepo:
            def __init__(self, db):
                pass

            async def sum_realized_pnl(self, ids):
                env.sum_calls.append(list(ids))
                return env.realized

        balance = AsyncMock(return_value={"output1": self.holdings, "output2": self.output2})
        patches = [patch.object(svc_mod, "get_stock_balance", balance)]
        if hasattr(svc_mod, "PoolRepository"):
            patches += [patch.object(svc_mod, "PoolRepository", FakePoolRepo),
                        patch.object(svc_mod, "TradeHistoryRepository", FakeHistoryRepo)]
        for p in patches:
            p.start()
        try:
            return await service.mapping_swing("u", ACCOUNT, mrkt)
        finally:
            for p in patches:
                p.stop()

    @staticmethod
    async def _coro(value):
        return value


def by_code(result):
    return {r["ST_CODE"]: r for r in result["list"]}


class LegacySummaryTest(unittest.IsolatedAsyncioTestCase):
    """풀을 쓰지 않는 계좌의 표시 — 보유 행은 증권사 값, 매입금액 기준 요약"""

    async def test_leftover_allocation_is_ignored_and_kis_values_are_used(self):
        """옛 배정금(INIT/CUR)이 남은 스윙도 더는 그 값으로 손익을 계산하지 않는다 — 보유는 KIS 값, 보유 없음은 0"""
        env = Env(
            swings=[row(1, "A"),   # 보유 중 (옛 배정금 700만이 남아 있음)
                    row(2, "B")],  # 보유 없음 — 옛 계산이면 -3.0% 가 나오던 행
            holdings=[holding("A", 10, 700_000, 50_000, 7.69, 650_000)])
        r = await env.run()
        rows = by_code(r)
        self.assertEqual((rows["A"]["EVLU_PFLS_RT"], rows["A"]["EVLU_PFLS_AMT"]), (7.69, 50_000))
        self.assertEqual((rows["B"]["EVLU_PFLS_RT"], rows["B"]["EVLU_PFLS_AMT"]), (0.0, 0))
        s = r["summary"]
        self.assertEqual((s["TOTAL_PRINCIPAL"], s["TOTAL_PROFIT"], s["TOTAL_VALUE"]), (650_000, 50_000, 700_000))

    async def test_zero_allocation_swings_use_kis_values_as_before(self):
        """INIT=0 (외부 매수 자동 등록 종목): 보유 중엔 KIS 평가손익, 보유 없음은 0"""
        env = Env(swings=[row(1, "A"), row(2, "B")],
                  holdings=[holding("A", 10, 700_000, 50_000, 7.69, 650_000)])
        r = await env.run()
        rows = by_code(r)
        self.assertEqual((rows["A"]["EVLU_PFLS_RT"], rows["A"]["EVLU_PFLS_AMT"]), (7.69, 50_000))
        self.assertEqual((rows["B"]["EVLU_PFLS_RT"], rows["B"]["EVLU_PFLS_AMT"]), (0.0, 0))
        s = r["summary"]
        self.assertEqual((s["TOTAL_PRINCIPAL"], s["TOTAL_PROFIT"], s["TOTAL_VALUE"]), (650_000, 50_000, 700_000))
        self.assertEqual(s["TOTAL_PROFIT_RATE"], round(50_000 / 650_000 * 100, 2))

    async def test_no_pool_fields_leak_into_legacy_rows(self):
        env = Env(swings=[row(1, "A"), row(2, "B")],
                  holdings=[holding("A", 10, 700_000, 50_000, 7.69, 650_000)])
        for r in (await env.run())["list"]:
            self.assertNotIn("REALIZED_PFLS_AMT", r)


class PoolSummaryTest(unittest.IsolatedAsyncioTestCase):
    """풀 모드 — 풀이 켜진 계좌·시장의 모든 스윙 (배정금이 남아 있어도 풀 행으로 본다)"""

    def pool(self, **kw):
        return Env(pool_setting=PoolSetting.create(ACCOUNT, "KR", use_yn="Y"), **kw)

    async def test_flat_pool_row_shows_realized_pnl_of_stop_loss(self):
        """매도 후 행이 0 으로 사라지지 않는다 — 확정 손실이 행과 요약에 남는다"""
        env = self.pool(swings=[row(1, "A")], holdings=[], realized={1: D("-210000")})
        r = await env.run()
        a = by_code(r)["A"]
        self.assertEqual((a["EVLU_PFLS_AMT"], a["REALIZED_PFLS_AMT"]), (-210_000, -210_000))
        self.assertAlmostEqual(a["EVLU_PFLS_RT"], -2.1)            # 총평가 1,000만 원 대비
        self.assertEqual(r["summary"]["TOTAL_PROFIT"], -210_000)

    async def test_holding_pool_row_keeps_kis_position_return_and_adds_realized(self):
        env = self.pool(swings=[row(1, "A")],
                        holdings=[holding("A", 10, 700_000, 50_000, 7.69, 650_000)], realized={1: D("30000")})
        r = await env.run()
        a = by_code(r)["A"]
        self.assertEqual((a["EVLU_PFLS_RT"], a["EVLU_PFLS_AMT"]), (7.69, 50_000))   # 이 포지션의 수익률 그대로
        self.assertEqual(a["REALIZED_PFLS_AMT"], 30_000)                              # 지난 사이클의 확정분
        self.assertEqual(r["summary"]["TOTAL_PROFIT"], 80_000)                        # 평가 + 확정

    async def test_summary_is_relative_to_total_assets(self):
        """원금(추정) 기준: 원금 = 총평가 − 누적 손익, 수익률 = 누적 손익 ÷ 그 원금 (표시 분모 일치)"""
        env = self.pool(swings=[row(1, "A"), row(2, "B")],
                        holdings=[holding("A", 10, 700_000, 50_000, 7.69, 650_000)],
                        realized={1: D("30000"), 2: D("-210000")})
        s = (await env.run())["summary"]
        self.assertEqual(s["TOTAL_PROFIT"], -130_000)
        self.assertEqual(s["TOTAL_VALUE"], 10_000_000)
        self.assertEqual(s["TOTAL_PRINCIPAL"], 10_130_000)
        self.assertEqual(s["TOTAL_PROFIT_RATE"], round(-130_000 / 10_130_000 * 100, 2))   # −13만 ÷ 원금 1,013만 = −1.28
        self.assertEqual(s["TOTAL_INVESTMENT_AMOUNT"], 10_000_000)
        self.assertTrue(s["POOL_MODE"])

    async def test_all_swings_are_looked_up_regardless_of_allocation(self):
        env = self.pool(swings=[row(1, "A"), row(2, "B")], holdings=[])
        await env.run()
        self.assertEqual(env.sum_calls, [[1, 2]])

    async def test_row_with_leftover_allocation_in_pool_account_is_a_pool_row(self):
        """풀이 켜지면 배치가 이 스윙도 총자산 기준으로 사므로 배정금 계산(−3%) 대신 풀 행으로 보여준다"""
        env = self.pool(swings=[row(1, "A"), row(2, "B")],
                        holdings=[], realized={1: D("-210000"), 2: D("5000")})
        r = await env.run()
        rows = by_code(r)
        self.assertEqual(rows["A"]["REALIZED_PFLS_AMT"], -210_000)
        self.assertAlmostEqual(rows["A"]["EVLU_PFLS_RT"], -2.1)        # 총평가 1,000만 원 대비 (배정금 대비 −3% 아님)
        self.assertEqual(r["summary"]["TOTAL_PROFIT"], -205_000)       # −21만 + 5천, 원금 합산 없음
        self.assertTrue(r["summary"]["POOL_MODE"])

    async def test_holding_row_with_leftover_allocation_uses_kis_values_plus_realized(self):
        """배정금이 남은 보유 행도 풀 행 — (CUR+평가−INIT)/INIT 의 옛 계산(490,000·7.0%)이 아니라 KIS 값 + 확정 손익"""
        env = self.pool(swings=[row(1, "A")],
                        holdings=[holding("A", 10, 700_000, 50_000, 7.69, 650_000)], realized={1: D("-210000")})
        r = await env.run()
        a = by_code(r)["A"]
        self.assertEqual((a["EVLU_PFLS_AMT"], a["EVLU_PFLS_RT"]), (50_000, 7.69))
        self.assertEqual(a["REALIZED_PFLS_AMT"], -210_000)
        self.assertEqual(r["summary"]["TOTAL_PROFIT"], -160_000)       # 평가 +5만 + 확정 −21만, 배정금 대비 계산 아님

    async def test_nonpositive_principal_gives_no_rate_instead_of_wrong_sign(self):
        """입출금을 반영하지 않아 누적 이익이 총자산보다 클 수 있다 — 원금(총평가−이익)이 0 이하면 수익률 0"""
        env = self.pool(swings=[row(1, "A")], holdings=[], realized={1: D("5000000")},
                        output2={"tot_evlu_amt": "100000", "evlu_pfls_smtl_amt": "0", "dnca_tot_amt": "100000"})
        s = (await env.run())["summary"]
        self.assertLess(s["TOTAL_PRINCIPAL"], 0)
        self.assertEqual(s["TOTAL_PROFIT_RATE"], 0.0)

    async def test_pool_off_for_this_market_changes_nothing(self):
        """풀 설정이 꺼져 있으면(행은 있어도) 기존 방식"""
        env = Env(pool_setting=PoolSetting.create(ACCOUNT, "KR", use_yn="N"),
                  swings=[row(1, "B")], holdings=[], realized={1: D("-210000")})
        r = await env.run()
        self.assertEqual(by_code(r)["B"]["EVLU_PFLS_AMT"], 0)
        self.assertEqual(env.sum_calls, [])

    async def test_pool_lookup_failure_degrades_to_legacy_display(self):
        """풀 설정 조회가 실패해도 목록 전체가 실패하면 안 된다"""
        env = self.pool(swings=[row(1, "A")], holdings=[], pool_error=RuntimeError("db"))
        r = await env.run()
        self.assertEqual(by_code(r)["A"]["EVLU_PFLS_RT"], 0.0)       # 보유 없음 — 옛 배정금 계산(-3.0%)을 쓰지 않는다

    async def test_unsupported_cash_is_fully_legacy(self):
        """예수금을 산출하지 못한 미국 모의는 총평가에 현금이 없다 — 총자산 기준이 성립하지 않으므로
        행·요약 모두 기존 방식이다 (확정 손익 조회도 하지 않는다)"""
        us_item = {"pdno": "AAPL", "prdt_name": "Apple", "hldg_qty": "10", "evlu_amt": "1000",
                   "evlu_pfls_amt": "100", "evlu_pfls_rt": "11.11", "pchs_amt": "900", "prpr": "101",
                   "ovrs_excg_cd": "NASD"}
        env = self.pool(swings=[row(1, "AAPL", mrkt="NAS")], holdings=[], realized={1: D("500")})
        env.pool_setting = PoolSetting.create(ACCOUNT, "US", use_yn="Y")
        with patch.object(svc_mod.foreign_api, "get_us_holdings",
                          AsyncMock(return_value={"output1": [us_item], "output2": {}})), \
             patch.object(svc_mod.foreign_api, "get_present_balance", AsyncMock(return_value=None)):
            r = await env.run(mrkt="NAS")
        a = by_code(r)["AAPL"]
        self.assertNotIn("REALIZED_PFLS_AMT", a)
        self.assertEqual(env.sum_calls, [], "현금 미지원인데 풀 손익을 조회함")
        s = r["summary"]
        self.assertIsNone(s["CASH_ASSET"])
        self.assertEqual((s["TOTAL_PRINCIPAL"], s["TOTAL_PROFIT"]), (900, 100.0))      # 기존: 매입금액 기준
        self.assertEqual(s["TOTAL_PROFIT_RATE"], round(100 / 900 * 100, 2))
        self.assertFalse(s["POOL_MODE"])                                                # 현금 미지원 → 완전 기존 방식


def inactive(r):
    return {**r, "USE_YN": "N"}


class SortAndParkingTest(unittest.IsolatedAsyncioTestCase):
    """목록 정렬(보유 → 활성 대기 → 비활성)과 요약의 파킹 ETF 표시"""

    def parking_pool(self, use="Y"):
        return PoolSetting.create(ACCOUNT, "KR", use_yn=use)

    async def test_order_is_held_then_waiting_then_inactive(self):
        swings = [inactive(row(1, "D")), row(2, "E"), row(3, "B"),
                  inactive(row(4, "C")), row(5, "A")]
        holdings = [holding("B", 10, 500_000, 1, 1, 499_999), holding("C", 5, 900_000, 1, 1, 899_999)]
        r = await Env(swings=swings, holdings=holdings).run()
        self.assertEqual([x["ST_CODE"] for x in r["list"]], ["C", "B", "A", "E", "D"])
        # 보유 중이면 비활성이어도 맨 위 그룹(평가금액 큰 순), 활성 대기는 이름순, 보유 없는 비활성은 마지막

    async def test_order_is_applied_to_pool_accounts_too(self):
        swings = [row(1, "A"), row(2, "B")]
        env = Env(swings=swings, holdings=[holding("B", 1, 70_000, 1, 1, 69_999)],
                  pool_setting=PoolSetting.create(ACCOUNT, "KR", use_yn="Y"))
        self.assertEqual([x["ST_CODE"] for x in (await env.run())["list"]], ["B", "A"])

    async def test_parking_is_in_summary_not_in_the_list(self):
        etf = holding("459580", 80, 86_000_000, 20_000, 0.02, 85_980_000)
        etf["prdt_name"] = "KODEX CD금리액티브(합성)"
        env = Env(swings=[row(1, "A")], holdings=[etf], pool_setting=self.parking_pool())
        r = await env.run()
        self.assertEqual(r["summary"]["PARKING"], {"PARK_CODE": "459580", "PARK_NM": "KODEX CD금리액티브(합성)",
                                                   "QTY": 80, "VALUE": 86_000_000, "PROFIT": 20_000})
        self.assertEqual([x["ST_CODE"] for x in r["list"]], ["A"], "파킹 ETF 는 스윙이 아니라 목록에 자동 등록하지 않는다")

    async def test_no_parking_field_value_when_not_held_or_pool_off(self):
        etf = holding("459580", 80, 86_000_000, 20_000, 0.02, 85_980_000)
        cases = {
            "풀 꺼짐": Env(swings=[row(1, "A")], holdings=[etf], pool_setting=self.parking_pool(use="N")),
            "보유 없음": Env(swings=[row(1, "A")], holdings=[], pool_setting=self.parking_pool()),
            "풀 설정 없음": Env(swings=[row(1, "A")], holdings=[etf]),
        }
        for name, env in cases.items():
            self.assertIsNone((await env.run())["summary"]["PARKING"], name)

    async def test_pool_lookup_failure_hides_parking_instead_of_failing_the_list(self):
        etf = holding("459580", 80, 86_000_000, 20_000, 0.02, 85_980_000)
        env = Env(swings=[row(1, "A")], holdings=[etf], pool_setting=self.parking_pool(), pool_error=RuntimeError("db"))
        r = await env.run()
        self.assertIsNone(r["summary"]["PARKING"])
        self.assertEqual(len(r["list"]), 1)


class RealizedPnlQueryTest(unittest.IsolatedAsyncioTestCase):
    """sum_realized_pnl 조회 조건 — 매수 거래가 섞이거나 다른 스윙이 합쳐지면 확정 손익이 틀어진다"""

    async def run_query(self, ids):
        from app.domain.trade_history.repository import TradeHistoryRepository

        captured = {}

        class FakeResult:
            def all(self):
                return [(1, 150), (2, -20.5)]

        class FakeDB:
            async def execute(self, query):
                captured["sql"] = str(query.compile(compile_kwargs={"literal_binds": True}))
                return FakeResult()

        return await TradeHistoryRepository(FakeDB()).sum_realized_pnl(ids), captured.get("sql")

    async def test_sells_only_grouped_by_swing(self):
        result, sql = await self.run_query([1, 2])
        self.assertIn('"TRADE_HISTORY"."TRADE_TYPE" = \'S\'', sql)
        self.assertIn('"TRADE_HISTORY"."SWING_ID" IN (1, 2)', sql)
        self.assertIn("GROUP BY", sql)
        self.assertEqual(result, {1: D("150"), 2: D("-20.5")})

    async def test_empty_ids_do_not_query(self):
        result, sql = await self.run_query([])
        self.assertEqual((result, sql), ({}, None))


if __name__ == "__main__":
    unittest.main()
