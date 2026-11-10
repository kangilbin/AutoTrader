"""
풀 매수 배치 연동 테스트

실행:
  uv run pytest tests/test_pool_batch.py -v

기존 시나리오 테스트(test_swing_trade_scenarios)와 같은 가짜 KIS·DB·Redis 위에서 process_single_swing·
풀 매수 함수·잡 전체를 돌린다. 확인할 것:
- 풀 대상 스윙은 신호가 확정돼도 주문하지 않고 후보만 모은다 (수량·한도는 사이클 끝의 할당기가 정한다)
- 풀 설정이 없거나 다른 계좌·시장이면 기존 방식 그대로다
- 풀 매수 결과(체결·부분 체결·미확인·편입·실패·건너뜀)가 스윙 상태·이력·Redis에 맞게 반영된다
- 총자산을 읽지 못하면 그 풀은 매수하지 않는다
"""
import json
from collections import defaultdict
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.domain.pool.entity import PoolSetting
from app.domain.swing.entity import SwingTrade
from app.domain.swing.trading import auto_swing_batch as batch
from app.domain.swing.trading import order_executor as oe
from app.domain.swing.trading import parking
from app.domain.swing.trading.pool_allocator import (
    BUY_ADOPTED, BUY_FAILED, BUY_FILLED, BUY_HALT, BUY_SKIPPED, BUY_UNCONFIRMED, BuyOutcome, EntryCandidate,
)
from app.domain.swing.trading.pool_state import AccountSnapshot
from app.domain.swing.trading.strategies.single_ema_strategy import SingleEMAStrategy
from tests.test_swing_trade_scenarios import (
    FakeTradeService, Market, Row, SwingScenarioBase, _coro,
)

ACCOUNT = "12345678-01"


# 이 파일의 숫자(250주 등)는 분산형 구성(0.10%·30종목·10%) 기준이다 — 기본값이 바뀌어도 흔들리지 않게 값을 명시한다.
BASE = dict(risk_pct="0.0010", max_positions=30, max_position_pct="0.10", max_open_risk_pct="0.08")


def pool_setting(market="US", **kw):
    return PoolSetting.create(ACCOUNT, market, use_yn="Y", **{**BASE, **kw})


def candidate(**kw):
    base = dict(swing_id=1, user_id="u1", account_no=ACCOUNT, st_code="AAPL", mrkt_code="NAS",
                price=101.0, conviction=0.6, atr=2.0, avg_daily_amount=1e8, reasons=("EMA돌파",))
    base.update(kw)
    return EntryCandidate(**base)


class PoolBase(SwingScenarioBase):
    INIT_AMOUNT = 0     # 풀 모드 스윙은 INIT_AMOUNT = 0 (배정금 없음)

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.notified = []
        batch._fire_trade_notification = lambda *a, **k: self.notified.append((a, k))

        # 파킹은 풀이 켜져 있으면 항상 거친다 — 파킹 ETF 가 없는 계좌(평가금 0, 현금 충분)가 기본 상황이다.
        # 파킹 동작 자체는 PoolParkingRunTest / test_parking.py 가 따로 검증하므로 여기서는 무동작으로 둔다.
        for name, mock in (("acquire_pool_lock", AsyncMock(return_value="token")), ("release_pool_lock", AsyncMock()),
                           ("fetch_park_value", AsyncMock(return_value=0.0)),
                           ("ensure_cash", AsyncMock(return_value=parking.EnsureCashResult(parking.ENSURE_OK)))):
            patcher = patch.object(parking, name, mock)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.rollbacks = 0

        async def rollback():
            self.rollbacks += 1
        self.db.rollback = rollback        # 기본 FakeDB 는 rollback 을 오류로 본다 — 실패 경로 테스트용

    async def cycle(self, settings, candidates=None, n=1):
        """process_single_swing n번 — 후보 목록은 잡처럼 사이클마다 새로 만든다 (마지막 것을 돌려준다)"""
        candidates = candidates if candidates is not None else defaultdict(list)
        for _ in range(n):
            candidates = defaultdict(list)
            await batch.process_single_swing(Row(), self.redis, settings, candidates)
        return candidates


class PoolCollectTest(PoolBase):
    async def test_pool_swing_collects_candidate_and_does_not_order(self):
        settings = {(ACCOUNT, "US"): pool_setting()}
        first = await self.cycle(settings)
        self.assertEqual(first[(ACCOUNT, "US")], [], "연속 확인 1회차인데 후보가 됨")

        second = await self.cycle(settings)           # 2회 연속 충족 → 후보
        [c] = second[(ACCOUNT, "US")]
        self.assertEqual((c.swing_id, c.user_id, c.account_no, c.st_code), (1, "u1", ACCOUNT, "AAPL"))
        self.assertEqual((c.price, c.atr, c.avg_daily_amount), (101.0, 2.0, 100_000_000.0))
        self.assertGreaterEqual(c.conviction, SingleEMAStrategy.MIN_CONVICTION)
        self.assertLessEqual(c.conviction, 1.0)
        self.assertEqual(c.reasons, ("EMA돌파",))
        # 주문도 상태 변화도 없다 — 수량·한도는 사이클 끝의 할당기가 정한다
        self.assertEqual(Market.orders, [])
        self.assertEqual((self.swing.SIGNAL, self.swing.HOLD_QTY or 0), (0, 0))

    async def test_candidate_is_recollected_every_cycle_while_signal_holds(self):
        """한도에 걸려 못 샀어도 신호 상태가 유지돼 다음 사이클에 다시 후보가 된다 (늦은 진입)"""
        settings = {(ACCOUNT, "US"): pool_setting()}
        await self.cycle(settings, n=2)
        again = await self.cycle(settings)
        self.assertEqual(len(again[(ACCOUNT, "US")]), 1)

    async def test_pool_off_never_buys_even_with_legacy_allocation(self):
        """풀이 꺼져 있으면(설정 없음) 사지 않는다 — 매수는 총자산 기준 풀 방식 하나뿐"""
        await self.cycle(None, n=3)
        self.assertIn(ACCOUNT, Market.seen_accounts, "사이클이 시세 조회까지 가지 못함 (주문 없음이 우연일 수 있다)")
        self.assertEqual(Market.orders, [])
        self.assertEqual(self.swing.SIGNAL, 0)

    async def test_other_account_or_market_settings_do_not_apply(self):
        other = {("99999999-01", "US"): pool_setting(), (ACCOUNT, "KR"): PoolSetting.create(ACCOUNT, "KR", use_yn="Y")}
        candidates = await self.cycle(other, n=3)
        self.assertEqual(Market.orders, [])
        self.assertEqual([c for v in candidates.values() for c in v], [], "다른 계좌·시장의 풀 설정이 이 스윙에 적용됨")

    async def test_pool_swing_with_zero_allocation_never_buys_without_pool(self):
        """풀 설정을 못 읽어 빈 값으로 폴백하면(INIT=0) 수량 0 → 매수 없음 — 엉뚱한 크기로 사지 않는다"""
        await self.cycle({}, n=3)
        self.assertIn(ACCOUNT, Market.seen_accounts, "사이클이 시세 조회까지 가지 못함 (주문 없음이 우연일 수 있다)")
        self.assertEqual(Market.orders, [])


class PoolBuyTest(PoolBase):
    async def buy(self, qty=50, **kw):
        return await batch._pool_buy(candidate(**kw), qty, self.redis)

    async def test_filled(self):
        out = await self.buy(50)
        self.assertEqual((out.status, out.qty), (BUY_FILLED, 50))
        self.assertGreater(out.avg_price, 0)
        self.assertEqual((self.swing.SIGNAL, self.swing.HOLD_QTY), (1, 50))
        self.assertEqual([o["qty"] for o in Market.buys()], [50])
        self.assertEqual(len(FakeTradeService.of("B")), 1, "매수 이력이 1건이 아님")
        # 풀 모드 CUR_AMOUNT 는 INIT=0 기준 누적 손익처럼 움직인다 — 매수 대금만큼 차감
        self.assertAlmostEqual(float(self.swing.CUR_AMOUNT), -out.avg_price * 50, delta=0.01)
        self.assertEqual(len(self.notified), 1, "매수 완료 푸시가 1회가 아님")
        self.assertEqual(self.rollbacks, 0)

    async def test_partial_fill_reports_actual_quantity(self):
        Market.fill_ratio = 0.4
        out = await self.buy(50)
        self.assertEqual((out.status, out.qty), (BUY_FILLED, 20))
        self.assertEqual((self.swing.SIGNAL, self.swing.HOLD_QTY), (1, 20))

    async def test_state_changed_since_candidate_is_skipped(self):
        for mutate in (lambda s: setattr(s, "SIGNAL", 1), lambda s: setattr(s, "USE_YN", "N"),
                       lambda s: setattr(s, "HOLD_QTY", 5)):
            Market.reset()
            self.swing.SIGNAL, self.swing.USE_YN, self.swing.HOLD_QTY = 0, "Y", 0
            mutate(self.swing)
            out = await self.buy(50)
            self.assertEqual(out.status, BUY_SKIPPED)
            self.assertEqual(Market.orders, [], "상태가 바뀐 스윙에 주문이 나감")

    async def test_existing_broker_holding_is_adopted_not_bought(self):
        """이미 실보유 중이면 중복 매수하지 않고 편입 — 매수 완료 푸시도 없다"""
        self.broker_position = lambda: {"qty": 30, "avg_price": 95.0, "prpr": 101.0}
        out = await self.buy(50)
        self.assertEqual((out.status, out.qty, out.avg_price), (BUY_ADOPTED, 30, 95.0))
        self.assertEqual((self.swing.SIGNAL, self.swing.HOLD_QTY), (1, 30))
        self.assertEqual(Market.orders, [])
        self.assertEqual(self.notified, [])

    async def test_broker_lookup_failure_blocks_buy(self):
        self.broker_position = lambda: None
        out = await self.buy(50)
        self.assertEqual(out.status, BUY_FAILED)
        self.assertEqual((Market.orders, self.swing.SIGNAL), ([], 0))
        self.assertEqual(self.rollbacks, 1)

    async def test_rejected_order_is_failure(self):
        async def reject(user_id, order, db, account_no=None):
            return {"rt_cd": "1", "msg_cd": "X", "msg1": "주문 거부"}
        oe.foreign_api.place_order_api = reject
        out = await self.buy(50)
        self.assertEqual(out.status, BUY_FAILED)
        self.assertEqual((self.swing.SIGNAL, self.swing.HOLD_QTY or 0), (0, 0))
        self.assertEqual(FakeTradeService.of("B"), [])

    async def test_unconfirmed_order_is_tracked_next_cycle_not_recorded(self):
        Market.fill_steps = [0.0]                    # 체결 조회가 계속 0건
        out = await self.buy(50)
        self.assertEqual(out.status, BUY_UNCONFIRMED)
        self.assertIn("pending_order:1", self.redis.store)
        self.assertEqual(json.loads(self.redis.store["pending_order:1"])["mrkt_code"], "NAS")
        self.assertEqual((self.swing.SIGNAL, self.swing.HOLD_QTY or 0), (0, 0), "미확인인데 상태가 바뀜")
        self.assertEqual(FakeTradeService.of("B"), [], "미확인인데 이력이 저장됨")
        self.assertEqual(len(Market.buys()), 1)

    async def test_failure_after_order_placed_is_reported_unconfirmed(self):
        """주문은 나갔는데 상태 반영이 실패 — 돈을 안 썼다고 알리면 안 된다 (다음 사이클 편입이 복구)"""
        with patch.object(SwingTrade, "transition_to_buy", side_effect=RuntimeError("db down")):
            out = await self.buy(50)
        self.assertEqual(out.status, BUY_UNCONFIRMED)
        self.assertEqual(len(Market.buys()), 1)
        self.assertEqual(self.rollbacks, 1)

    async def test_error_while_confirming_a_possibly_sent_order_is_unconfirmed_not_failed(self):
        """체결 확인 조회가 KIS 오류로 터져도 주문은 나갔을 수 있다 — '실패'로 알리면 같은 사이클의 다음 후보가 같은 현금을 또 쓴다"""
        with patch.object(oe.SwingOrderExecutor, "execute_buy", AsyncMock(side_effect=RuntimeError("KIS 5xx"))):
            outcome = await batch._pool_buy(candidate(), 250, self.redis, equity=1_000_000.0)
        self.assertEqual(outcome.status, BUY_UNCONFIRMED)
        self.assertEqual((self.swing.SIGNAL, self.swing.HOLD_QTY or 0), (0, 0), "상태는 반영하지 않는다 (다음 사이클 편입이 복구)")

    async def test_failure_before_order_is_plain_failure(self):
        async def boom(*a, **k):
            raise RuntimeError("조회 실패")
        with patch.object(batch, "_adopt_before_buy", boom):
            out = await self.buy(50)
        self.assertEqual(out.status, BUY_FAILED)
        self.assertEqual(Market.orders, [])


class PoolRunTest(PoolBase):
    """_run_pool_entries — 청산이 끝난 뒤 스냅샷을 읽고 할당기를 실행"""

    def patched(self, snapshot, positions=()):
        return (patch.object(batch, "fetch_account_snapshot", AsyncMock(return_value=snapshot)),
                patch.object(batch, "load_positions", AsyncMock(return_value=list(positions))))

    async def run_pool(self, snapshot, candidates=None, settings=None):
        snap_patch, pos_patch = self.patched(snapshot)
        key = (ACCOUNT, "US")
        with snap_patch, pos_patch:
            await batch._run_pool_entries(
                "TEST", settings if settings is not None else {key: pool_setting()},
                {key: candidates if candidates is not None else [candidate()]}, self.redis)

    async def test_orders_risk_based_quantity(self):
        """총자산 100만 × 0.10% = 1,000 ÷ 주당 손절폭 4(가격 101, 청산선 97) = 250주"""
        await self.run_pool(AccountSnapshot("US", 1_000_000.0, 1_000_000.0))
        self.assertEqual([o["qty"] for o in Market.buys()], [250])
        self.assertEqual((self.swing.SIGNAL, self.swing.HOLD_QTY), (1, 250))

    async def test_trade_history_reason_is_relative_to_total_assets(self):
        """체결 이력 사유가 배정금(INIT=0이라 불가)이 아니라 총자산 대비 — 250주 × 약 101 ÷ 100만 = 2.5%"""
        await self.run_pool(AccountSnapshot("US", 1_000_000.0, 1_000_000.0))
        [buy] = FakeTradeService.of("B")
        self.assertIn("투입 2.5% (총자산 대비)", buy["reasons"])

    async def test_no_snapshot_means_no_buy(self):
        """총자산을 읽지 못하면(환율 미수신 등) 그 풀은 이번 사이클 매수하지 않는다"""
        await self.run_pool(None)
        self.assertEqual((Market.orders, self.swing.SIGNAL), ([], 0))

    async def test_snapshot_error_does_not_raise_or_buy(self):
        with patch.object(batch, "fetch_account_snapshot", AsyncMock(side_effect=RuntimeError("KIS 장애"))):
            await batch._run_pool_entries("TEST", {(ACCOUNT, "US"): pool_setting()},
                                          {(ACCOUNT, "US"): [candidate()]}, self.redis)
        self.assertEqual(Market.orders, [])

    async def test_insufficient_cash_means_no_buy(self):
        await self.run_pool(AccountSnapshot("US", 1_000_000.0, 1_000.0))
        self.assertEqual(Market.orders, [])

    async def test_candidates_without_pool_setting_are_ignored(self):
        await self.run_pool(AccountSnapshot("US", 1_000_000.0, 1_000_000.0), settings={})
        self.assertEqual(Market.orders, [])

    async def test_empty_candidates_do_not_touch_kis(self):
        snap = AsyncMock(return_value=AccountSnapshot("US", 1.0, 1.0))
        with patch.object(batch, "fetch_account_snapshot", snap):
            await batch._run_pool_entries("TEST", {(ACCOUNT, "US"): pool_setting()}, {(ACCOUNT, "US"): []}, self.redis)
        snap.assert_not_called()


class PoolSettingsLoadTest(PoolBase):
    def repo(self, rows=None, error=None):
        class FakePoolRepo:
            def __init__(self, db):
                pass

            async def find_all_active(self):
                if error:
                    raise error
                return rows
        return patch.object(batch, "PoolRepository", FakePoolRepo)

    async def test_only_requested_market(self):
        rows = [PoolSetting.create("A", "KR", use_yn="Y"), PoolSetting.create("B", "US", use_yn="Y")]
        with self.repo(rows):
            kr, us = await batch._load_pool_settings("KR"), await batch._load_pool_settings("US")
        self.assertEqual((list(kr), list(us)), ([("A", "KR")], [("B", "US")]))

    async def test_failure_falls_back_to_empty(self):
        with self.repo(error=RuntimeError("db down")):
            self.assertEqual(await batch._load_pool_settings("US"), {})


class PoolJobTest(PoolBase):
    """잡 전체 — 설정 읽기 → 신호 평가·후보 수집 → 청산 후 할당기 → 주문"""

    def wire(self, settings):
        swing = self.swing
        test = self

        async def active_overseas():
            return [Row()]

        batch.SwingService = lambda _db: type("Svc", (), {
            "db": _db,
            "repo": type("R", (), {"find_by_id": staticmethod(lambda _id: _coro(swing))})(),
            "fetch_broker_position": staticmethod(lambda *a, **k: _coro(test.broker_position())),
            "get_active_overseas_swings": staticmethod(active_overseas),
        })()
        repo = type("PR", (), {"__init__": lambda self, db: None,
                               "find_all_active": staticmethod(lambda: _coro(settings))})
        for p in (patch.object(batch, "PoolRepository", repo),
                  patch.object(batch.Redis, "get_connection", AsyncMock(return_value=self.redis)),
                  patch.object(batch, "fetch_account_snapshot",
                               AsyncMock(return_value=AccountSnapshot("US", 1_000_000.0, 1_000_000.0))),
                  patch.object(batch, "load_positions", AsyncMock(return_value=[]))):
            p.start()
            self.addCleanup(p.stop)

    async def test_us_job_buys_through_pool_after_confirmation(self):
        self.wire([pool_setting()])
        await batch.us_trade_job()
        self.assertEqual(Market.orders, [], "연속 확인 1회차에 주문이 나감")
        await batch.us_trade_job()
        self.assertEqual([o["qty"] for o in Market.buys()], [250])
        self.assertEqual(self.swing.SIGNAL, 1)

    async def test_us_job_without_pool_settings_uses_legacy_path(self):
        """풀 설정이 없으면 기존 방식 — 배정금이 0 이면 수량 0 이라 사지 않는다"""
        self.wire([])
        await batch.us_trade_job()
        await batch.us_trade_job()
        self.assertIn(ACCOUNT, Market.seen_accounts, "잡이 스윙 처리까지 가지 못함 (주문 없음이 우연일 수 있다)")
        self.assertEqual(Market.orders, [])


def parking_setting():
    return pool_setting()


class PoolParkingRunTest(PoolBase):
    """_run_pool_entries + 파킹 — 현금이 모자라면 파킹 ETF를 팔아 만들고, 못 만들면 그 매수만 보류한다"""

    async def run_pool(self, snapshot, setting, ensure_ok=True, park_value=0.0, lock="token"):
        self.acquire = AsyncMock(return_value=lock)
        self.release = AsyncMock()
        kind = ensure_ok if isinstance(ensure_ok, str) else (parking.ENSURE_OK if ensure_ok else parking.ENSURE_ERROR)
        self.ensure = AsyncMock(return_value=parking.EnsureCashResult(kind, message="x"))
        self.value = AsyncMock(return_value=park_value)
        key = (ACCOUNT, "US")
        with patch.object(batch, "fetch_account_snapshot", AsyncMock(return_value=snapshot)) as snap, \
             patch.object(batch, "load_positions", AsyncMock(return_value=[])), \
             patch.object(parking, "acquire_pool_lock", self.acquire), \
             patch.object(parking, "release_pool_lock", self.release), \
             patch.object(parking, "ensure_cash", self.ensure), \
             patch.object(parking, "fetch_park_value", self.value):
            await batch._run_pool_entries("TEST", {key: setting}, {key: [candidate()]}, self.redis)
        return snap

    async def test_sufficient_cash_buys_after_a_noop_ensure(self):
        """현금이 충분하면 해제 없이 그대로 산다 — 파킹은 풀이 켜져 있으면 항상 거치지만 팔 일이 없다"""
        await self.run_pool(AccountSnapshot("US", 1_000_000.0, 1_000_000.0), pool_setting())
        self.ensure.assert_awaited_once()
        self.assertEqual([o["qty"] for o in Market.buys()], [250])

    async def test_park_value_counts_as_buying_power_and_cash_is_ensured_before_buy(self):
        # 현금 1,000 뿐이라 파킹 없이는 못 사지만, 파킹 평가금 99.9만을 더하면 250주(약 2.5만) 매수 가능
        await self.run_pool(AccountSnapshot("US", 1_000_000.0, 1_000.0), parking_setting(), park_value=999_000.0)
        self.ensure.assert_awaited_once()
        needed = self.ensure.await_args.args[3]
        self.assertIsNotNone(self.ensure.await_args.args[4], "미확인 때 파킹 버튼을 막을 수 있게 redis 를 넘긴다")
        self.assertAlmostEqual(needed, 250 * 101.0 * 1.01)
        self.assertEqual([o["qty"] for o in Market.buys()], [250])
        self.assertEqual(self.swing.SIGNAL, 1)

    async def test_without_park_value_insufficient_cash_still_blocks(self):
        await self.run_pool(AccountSnapshot("US", 1_000_000.0, 1_000.0), parking_setting(), park_value=0.0)
        self.ensure.assert_not_called()
        self.assertEqual(Market.orders, [])

    async def test_failed_release_skips_the_buy_and_keeps_signal(self):
        await self.run_pool(AccountSnapshot("US", 1_000_000.0, 1_000.0), parking_setting(),
                            ensure_ok=False, park_value=999_000.0)
        self.assertEqual((Market.orders, self.swing.SIGNAL, self.swing.HOLD_QTY or 0), ([], 0, 0))

    async def test_busy_lock_skips_everything_and_does_not_release_what_it_never_took(self):
        snap = await self.run_pool(AccountSnapshot("US", 1_000_000.0, 1_000_000.0), parking_setting(), lock=None)
        snap.assert_not_called()
        self.assertEqual(Market.orders, [])
        self.release.assert_not_called()

    async def test_lock_is_released_after_run_and_after_failure(self):
        await self.run_pool(AccountSnapshot("US", 1_000_000.0, 1_000_000.0), parking_setting())
        self.release.assert_awaited_once()
        self.assertEqual(self.release.await_args.args[1:], (ACCOUNT, "US", "token"))
        self.acquire = AsyncMock(return_value="t2")
        self.release = AsyncMock()
        with patch.object(batch, "fetch_account_snapshot", AsyncMock(side_effect=RuntimeError("KIS 장애"))), \
             patch.object(parking, "acquire_pool_lock", self.acquire), \
             patch.object(parking, "release_pool_lock", self.release):
            await batch._run_pool_entries("TEST", {(ACCOUNT, "US"): parking_setting()},
                                          {(ACCOUNT, "US"): [candidate()]}, self.redis)
        self.release.assert_awaited_once()

    async def test_insufficient_release_skips_only_that_candidate_and_is_not_a_failure(self):
        """한 후보가 비싸 못 사는 것은 실패가 아니다 — 연속 3회 실패 중단에 세지 않는다"""
        self.refresh = AsyncMock()
        with patch.object(parking, "refresh_pool_lock", self.refresh):
            await self.run_pool(AccountSnapshot("US", 1_000_000.0, 1_000.0), parking_setting(),
                                ensure_ok=parking.ENSURE_INSUFFICIENT, park_value=999_000.0)
        self.assertEqual((Market.orders, self.swing.SIGNAL), ([], 0))
        self.refresh.assert_awaited()

    async def test_halt_stops_the_rest_of_the_cycle(self):
        from app.domain.swing.trading.pool_allocator import run_pool_entries
        calls = []

        async def buy(c, qty):
            calls.append(c.st_code)
            return BuyOutcome(BUY_HALT) if c.st_code == "AAA" else BuyOutcome(BUY_FILLED, qty, c.price)
        cands = [candidate(st_code="AAA", conviction=0.9, swing_id=1), candidate(st_code="BBB", conviction=0.5, swing_id=2)]
        result = await run_pool_entries(parking_setting(), cands, AccountSnapshot("US", 1_000_000.0, 1_000_000.0), [], buy)
        self.assertEqual(calls, ["AAA"], "중단 요청 뒤에는 다음 후보를 시도하지 않는다")
        self.assertEqual(result.skipped["aborted"], 1)

    async def test_release_is_not_attempted_when_candidate_state_changed(self):
        """스윙 상태가 이미 바뀌어 사지 않을 후보 때문에 파킹 ETF를 팔면 안 된다"""
        self.swing.SIGNAL = 1
        self.swing.HOLD_QTY = 5
        await self.run_pool(AccountSnapshot("US", 1_000_000.0, 1_000.0), parking_setting(), park_value=999_000.0)
        self.ensure.assert_not_called()
        self.assertEqual(Market.orders, [])

    async def test_lock_release_error_does_not_escape_the_batch(self):
        self.acquire = AsyncMock(return_value="t")
        self.release = AsyncMock(side_effect=RuntimeError("redis down"))
        with patch.object(batch, "fetch_account_snapshot", AsyncMock(return_value=None)), \
             patch.object(parking, "acquire_pool_lock", self.acquire), \
             patch.object(parking, "release_pool_lock", self.release):
            await batch._run_pool_entries("TEST", {(ACCOUNT, "US"): parking_setting()},
                                          {(ACCOUNT, "US"): [candidate()]}, self.redis)
        self.release.assert_awaited_once()

    async def test_batch_maps_halt_so_the_second_candidate_never_releases_more_etf(self):
        """연결부 검증: 첫 후보의 해제 결과가 HALT 면 다음 후보는 ensure_cash(= 추가 ETF 매도)까지 가지 않는다"""
        self.acquire, self.release = AsyncMock(return_value="t"), AsyncMock()
        self.ensure = AsyncMock(return_value=parking.EnsureCashResult(parking.ENSURE_HALT, message="x"))
        key = (ACCOUNT, "US")
        cands = [candidate(st_code="AAA", conviction=0.9), candidate(st_code="BBB", conviction=0.5)]
        with patch.object(batch, "fetch_account_snapshot", AsyncMock(return_value=AccountSnapshot("US", 1_000_000.0, 1_000.0))), \
             patch.object(batch, "load_positions", AsyncMock(return_value=[])), \
             patch.object(parking, "acquire_pool_lock", self.acquire), patch.object(parking, "release_pool_lock", self.release), \
             patch.object(parking, "refresh_pool_lock", AsyncMock()), patch.object(parking, "ensure_cash", self.ensure), \
             patch.object(parking, "fetch_park_value", AsyncMock(return_value=999_000.0)):
            await batch._run_pool_entries("TEST", {key: parking_setting()}, {key: cands}, self.redis)
        self.assertEqual(self.ensure.await_count, 1)
        self.assertEqual(Market.orders, [])


if __name__ == "__main__":
    import unittest
    unittest.main()
