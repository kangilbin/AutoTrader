"""파킹 ETF (domain/swing/trading/parking.py) — 외부 호출은 모두 모킹, 실제 KIS 호출 없음

실행: uv run pytest tests/test_parking.py -v
"""
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.domain.pool.entity import PoolSetting
from app.domain.swing.trading import parking
from app.domain.swing.trading.pool_state import AccountSnapshot


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


def kr_setting(use="Y"):
    return PoolSetting.create("ACC", "KR", use_yn=use)


def snap(equity=100_000_000.0, cash=40_000_000.0):
    return AccountSnapshot(market="KR", equity=equity, cash=cash)


class PlanTest(unittest.TestCase):
    def test_park_qty_leaves_reserve_and_order_buffer(self):
        # 현금 4천만 − 남길 현금(총자산 1억 × 5% = 500만) = 3,500만 → 1주 10,100원(1% 여유)
        self.assertEqual(parking.plan_park_qty(100_000_000, 40_000_000, 0.05, 10_000), 35_000_000 // 10_100)

    def test_park_qty_zero_when_cash_below_reserve_or_bad_input(self):
        self.assertEqual(parking.plan_park_qty(100_000_000, 4_000_000, 0.05, 10_000), 0)
        self.assertEqual(parking.plan_park_qty(100_000_000, 40_000_000, 0.05, 0), 0)
        self.assertEqual(parking.plan_park_qty(0, 40_000_000, 0.05, 10_000), 0)

    def test_release_qty_covers_shortfall_with_buffer(self):
        self.assertEqual(parking.plan_release_qty(1_000_000, 500, 10_000), 102)   # ceil(1,000,000 / 9,900)

    def test_release_is_zero_when_whole_holding_cannot_cover(self):
        self.assertEqual(parking.plan_release_qty(1_000_000, 50, 10_000), 0)
        self.assertEqual(parking.plan_release_qty(0, 500, 10_000), 0)
        self.assertEqual(parking.plan_release_qty(1_000, 0, 10_000), 0)


class ParkTest(unittest.IsolatedAsyncioTestCase):
    def patches(self, snapshot=None, price=10_000.0, order=None):
        return [
            patch.object(parking, "fetch_account_snapshot", AsyncMock(return_value=snapshot)),
            patch.object(parking, "fetch_park_price", AsyncMock(return_value=price)),
            patch.object(parking.SwingOrderExecutor, "execute_plain", AsyncMock(return_value=order)),
        ]

    async def run_park(self, setting=None, redis=None, market_open=True, **kw):
        ps = self.patches(**kw)
        ps.append(patch("app.domain.swing.trading.auto_swing_batch.is_market_open", return_value=market_open))
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        redis = redis or FakeRedis()
        return await parking.park_idle_cash("u", None, redis, setting or kr_setting()), redis

    async def test_parks_and_releases_lock(self):
        order = {"success": True, "qty": 3465, "avg_price": 10_000, "amount": 34_650_000.0}
        result, redis = await self.run_park(snapshot=snap(), order=order)
        self.assertEqual((result.status, result.qty, result.amount), (parking.PARKED, 3465, 34_650_000.0))
        self.assertEqual(redis.store, {}, "락이 풀려야 한다")
        call = parking.SwingOrderExecutor.execute_plain.call_args
        self.assertEqual(call.args[0], "buy")
        self.assertEqual(call.args[6], "J")          # 국내 시장코드

    async def test_closed_market_places_no_order_and_takes_no_lock(self):
        result, redis = await self.run_park(snapshot=snap(), order={"success": True}, market_open=False)
        self.assertEqual(result.status, parking.CLOSED)
        self.assertIn("장 운영 시간", result.message)
        self.assertFalse(parking.SwingOrderExecutor.execute_plain.called)
        self.assertEqual(redis.store, {}, "장이 닫혀 있으면 락도 잡지 않는다")

    async def test_market_hours_are_checked_for_the_pool_market(self):
        us = PoolSetting.create("ACC", "US", use_yn="Y")
        with patch("app.domain.swing.trading.auto_swing_batch.is_market_open", return_value=False) as m:
            result = await parking.park_idle_cash("u", None, FakeRedis(), us)
        m.assert_called_once_with("NYS")
        self.assertEqual(result.status, parking.CLOSED)
        self.assertIn("미국", result.message)

    async def test_disabled_when_pool_off(self):
        result, _ = await self.run_park(setting=kr_setting(use="N"), snapshot=snap(), order={"success": True})
        self.assertEqual(result.status, parking.FAILED)
        self.assertFalse(parking.SwingOrderExecutor.execute_plain.called)

    async def test_busy_when_lock_held_and_does_not_steal_it(self):
        redis = FakeRedis()
        redis.store["pool_lock:ACC:KR"] = "someone-else"
        result, redis = await self.run_park(redis=redis, snapshot=snap(), order={"success": True})
        self.assertEqual(result.status, parking.BUSY)
        self.assertEqual(redis.store["pool_lock:ACC:KR"], "someone-else")
        self.assertFalse(parking.SwingOrderExecutor.execute_plain.called)

    async def test_nothing_to_park(self):
        result, _ = await self.run_park(snapshot=snap(cash=1_000_000), order={"success": True})
        self.assertEqual(result.status, parking.NOTHING)
        self.assertFalse(parking.SwingOrderExecutor.execute_plain.called)

    async def test_park_reads_cash_strictly(self):
        snap_mock = AsyncMock(return_value=None)
        with patch.object(parking, "fetch_account_snapshot", snap_mock), \
             patch("app.domain.swing.trading.auto_swing_batch.is_market_open", return_value=True):
            result = await parking.park_idle_cash("u", None, FakeRedis(), kr_setting())
        self.assertEqual(result.status, parking.FAILED)
        self.assertTrue(snap_mock.await_args.kwargs.get("strict"))

    async def test_snapshot_or_price_failure(self):
        result, _ = await self.run_park(snapshot=None, order={"success": True})
        self.assertEqual(result.status, parking.FAILED)
        result, _ = await self.run_park(snapshot=snap(), price=None, order={"success": True})
        self.assertEqual(result.status, parking.FAILED)

    async def test_order_failure_and_unconfirmed(self):
        result, redis = await self.run_park(snapshot=snap(), order={"success": False, "reason": "주문 거부"})
        self.assertEqual((result.status, result.message, redis.store), (parking.FAILED, "주문 거부", {}))
        result, redis = await self.run_park(snapshot=snap(), order={"success": True, "unconfirmed": True})
        self.assertEqual(result.status, parking.UNCONFIRMED)
        self.assertNotIn("pool_lock:ACC:KR", redis.store, "락은 풀려야 한다")
        self.assertIn("pool_park_pending:ACC:KR", redis.store, "미확인이면 버튼을 잠시 막는다")

    async def test_button_is_blocked_while_previous_order_is_unconfirmed(self):
        """체결 확인이 안 된 직후 또 누르면 같은 현금으로 두 번 살 수 있다 — 대기 표시가 있으면 주문하지 않는다"""
        redis = FakeRedis()
        redis.store["pool_park_pending:ACC:KR"] = "1"
        result, redis = await self.run_park(redis=redis, snapshot=snap(), order={"success": True})
        self.assertEqual(result.status, parking.BUSY)
        self.assertFalse(parking.SwingOrderExecutor.execute_plain.called)
        self.assertEqual(redis.store.get("pool_lock:ACC:KR"), None)

    async def test_unexpected_error_is_contained_and_lock_released(self):
        redis = FakeRedis()
        with patch.object(parking, "fetch_account_snapshot", AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("app.domain.swing.trading.auto_swing_batch.is_market_open", return_value=True):
            result = await parking.park_idle_cash("u", None, redis, kr_setting())
        self.assertEqual(result.status, parking.FAILED)
        self.assertEqual(redis.store, {})


class EnsureCashTest(unittest.IsolatedAsyncioTestCase):
    async def ensure(self, cashes, holding=(500, 10_000.0), order=None, needed=5_000_000.0):
        """cashes: 잔고를 읽을 때마다 순서대로 돌려줄 현금"""
        snaps = [None if c is None else snap(cash=c) for c in cashes]
        with patch.object(parking, "fetch_account_snapshot", AsyncMock(side_effect=snaps)), \
             patch.object(parking, "fetch_park_holding", AsyncMock(return_value=holding)), \
             patch.object(parking.SwingOrderExecutor, "execute_plain", AsyncMock(return_value=order)) as plain:
            result = await parking.ensure_cash("u", None, kr_setting(), needed)
        return result, plain

    async def test_enough_cash_sells_nothing(self):
        result, plain = await self.ensure([9_000_000.0])
        self.assertTrue(result.ok)
        self.assertEqual(result.kind, parking.ENSURE_OK)
        self.assertEqual(result.sold_qty, 0)
        self.assertFalse(plain.called)

    async def test_sells_only_shortfall_then_confirms_cash(self):
        # 현금 300만, 필요 500만 → 부족 200만 → ceil(2,000,000/9,900)=203주만 판다
        order = {"success": True, "qty": 203, "avg_price": 10_000, "amount": 2_030_000.0}
        result, plain = await self.ensure([3_000_000.0, 5_030_000.0], order=order)
        self.assertEqual((result.ok, result.sold_qty), (True, 203))
        self.assertEqual(plain.call_args.args[0], "sell")
        self.assertEqual(plain.call_args.args[4], 203)

    async def test_cash_still_short_after_sale_blocks_the_buy(self):
        """매도 직후 매수 가능 금액이 안 늘면(정산 대기) 실패로 돌려 이번 매수를 건너뛴다"""
        order = {"success": True, "qty": 203, "avg_price": 10_000, "amount": 2_030_000.0}
        result, _ = await self.ensure([3_000_000.0, 3_000_000.0], order=order)
        self.assertFalse(result.ok)
        self.assertEqual((result.kind, result.sold_qty), (parking.ENSURE_HALT, 203),
                         "해제 뒤에도 모자라면 정산 대기 가정이 틀린 것 — 후보마다 ETF를 더 팔지 않게 사이클을 멈춘다")

    async def test_no_park_holding_or_too_small_to_cover(self):
        result, plain = await self.ensure([3_000_000.0], holding=(0, 0.0))
        self.assertEqual(result.kind, parking.ENSURE_INSUFFICIENT, "모자란 것은 실패가 아니라 그 후보만 건너뛰는 사유")
        result, plain = await self.ensure([3_000_000.0], holding=(10, 10_000.0))   # 10만원어치로 200만 못 메움
        self.assertEqual(result.kind, parking.ENSURE_INSUFFICIENT)
        self.assertFalse(plain.called, "다 팔아도 못 사는 상황이면 팔지 않는다")

    async def test_sell_failure_is_error_and_unconfirmed_halts_the_cycle(self):
        result, _ = await self.ensure([3_000_000.0], order={"success": False, "reason": "거부"})
        self.assertEqual(result.kind, parking.ENSURE_ERROR)
        result, _ = await self.ensure([3_000_000.0], order={"success": True, "unconfirmed": True})
        self.assertEqual(result.kind, parking.ENSURE_HALT,
                         "미확인이면 다음 후보가 잔고를 다시 읽어도 반영 전일 수 있어 같은 ETF를 또 팔 수 있다")

    async def test_unconfirmed_sell_also_blocks_the_park_button(self):
        redis = FakeRedis()
        with patch.object(parking, "fetch_account_snapshot", AsyncMock(return_value=snap(cash=3_000_000.0))), \
             patch.object(parking, "fetch_park_holding", AsyncMock(return_value=(500, 10_000.0))), \
             patch.object(parking.SwingOrderExecutor, "execute_plain",
                          AsyncMock(return_value={"success": True, "unconfirmed": True})):
            result = await parking.ensure_cash("u", None, kr_setting(), 5_000_000.0, redis)
        self.assertEqual(result.kind, parking.ENSURE_HALT)
        self.assertIn("pool_park_pending:ACC:KR", redis.store)

    async def test_every_cash_read_is_strict_and_uses_the_orderable_amount(self):
        """해제 직후 재매수 가능 여부는 주문가능 금액으로 확인한다 — 조회 실패 시 예수금으로 대신하지 않는다"""
        snap_mock = AsyncMock(side_effect=[snap(cash=3_000_000.0), snap(cash=5_030_000.0)])
        order = {"success": True, "qty": 203, "avg_price": 10_000, "amount": 2_030_000.0}
        with patch.object(parking, "fetch_account_snapshot", snap_mock), \
             patch.object(parking, "fetch_park_holding", AsyncMock(return_value=(500, 10_000.0))), \
             patch.object(parking.SwingOrderExecutor, "execute_plain", AsyncMock(return_value=order)):
            result = await parking.ensure_cash("u", None, kr_setting(), 5_000_000.0)
        self.assertTrue(result.ok)
        self.assertEqual(snap_mock.await_count, 2)
        for call in snap_mock.await_args_list:
            self.assertTrue(call.kwargs.get("strict"), "현금 확인은 strict 여야 한다")

    async def test_error_after_the_sell_may_have_been_sent_halts_instead_of_erroring(self):
        """매도 주문이 나갔을 수 있는데 확인이 터지면 다음 후보가 같은 ETF 를 또 팔 수 있다 — ERROR 가 아니라 HALT"""
        redis = FakeRedis()
        # (1) execute_plain 자체가 예외
        with patch.object(parking, "fetch_account_snapshot", AsyncMock(return_value=snap(cash=3_000_000.0))), \
             patch.object(parking, "fetch_park_holding", AsyncMock(return_value=(500, 10_000.0))), \
             patch.object(parking.SwingOrderExecutor, "execute_plain", AsyncMock(side_effect=RuntimeError("KIS 5xx"))):
            r1 = await parking.ensure_cash("u", None, kr_setting(), 5_000_000.0, redis)
        self.assertEqual(r1.kind, parking.ENSURE_HALT)
        self.assertIn("pool_park_pending:ACC:KR", redis.store, "파킹 버튼도 잠시 막는다")
        # (2) 매도는 체결됐는데 직후 잔고 조회가 예외
        order = {"success": True, "qty": 203, "avg_price": 10_000, "amount": 2_030_000.0}
        with patch.object(parking, "fetch_account_snapshot",
                          AsyncMock(side_effect=[snap(cash=3_000_000.0), RuntimeError("조회 장애")])), \
             patch.object(parking, "fetch_park_holding", AsyncMock(return_value=(500, 10_000.0))), \
             patch.object(parking.SwingOrderExecutor, "execute_plain", AsyncMock(return_value=order)):
            r2 = await parking.ensure_cash("u", None, kr_setting(), 5_000_000.0)
        self.assertEqual(r2.kind, parking.ENSURE_HALT)
        # (3) 매도 주문 전의 예외는 그냥 ERROR
        with patch.object(parking, "fetch_account_snapshot", AsyncMock(side_effect=RuntimeError("장애"))):
            r3 = await parking.ensure_cash("u", None, kr_setting(), 5_000_000.0)
        self.assertEqual(r3.kind, parking.ENSURE_ERROR)

    async def test_unreadable_balance_or_holding_is_error(self):
        result, _ = await self.ensure([None])
        self.assertEqual(result.kind, parking.ENSURE_ERROR)
        result, _ = await self.ensure([3_000_000.0], holding=None)
        self.assertEqual(result.kind, parking.ENSURE_ERROR)

    async def test_exception_is_contained(self):
        with patch.object(parking, "fetch_account_snapshot", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await parking.ensure_cash("u", None, kr_setting(), 1.0)
        self.assertFalse(result.ok)


class LockTest(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_extends_only_own_lock(self):
        r = FakeRedis()
        calls = []
        orig = r.set

        async def spy(key, value, nx=False, ex=None):
            calls.append((key, value, nx, ex))
            return await orig(key, value, nx=nx, ex=ex)
        r.set = spy
        token = await parking.acquire_pool_lock(r, "ACC", "KR")
        calls.clear()
        await parking.refresh_pool_lock(r, "ACC", "KR", "not-mine")
        self.assertEqual(calls, [], "남의 락은 연장하지 않는다")
        await parking.refresh_pool_lock(r, "ACC", "KR", token)
        self.assertEqual(calls, [("pool_lock:ACC:KR", token, False, parking.LOCK_TTL)])
        r.store.clear()
        await parking.refresh_pool_lock(r, "ACC", "KR", token)
        self.assertEqual(r.store, {}, "이미 풀린(만료된) 락을 되살리지 않는다")

    async def test_release_only_own_token(self):
        r = FakeRedis()
        t1 = await parking.acquire_pool_lock(r, "ACC", "KR")
        self.assertIsNone(await parking.acquire_pool_lock(r, "ACC", "KR"))
        await parking.release_pool_lock(r, "ACC", "KR", "not-mine")
        self.assertIn("pool_lock:ACC:KR", r.store)
        await parking.release_pool_lock(r, "ACC", "KR", t1)
        self.assertEqual(r.store, {})
        self.assertIsNotNone(await parking.acquire_pool_lock(r, "ACC", "KR"))
        self.assertIsNotNone(await parking.acquire_pool_lock(r, "ACC", "US"), "시장이 다르면 별개 락")


# ── 서비스 (소유권·설정 확인·응답 형식) ────────────────────────────────────────
from app.domain.pool.service import PoolService
from app.exceptions import BusinessRuleError, NotFoundError


class _FakeAccountRepo:
    def __init__(self, owned=True):
        self.owned = owned

    async def find_auth_id_by_account_no(self, user_id, account_no):
        return 1 if self.owned else None


class _FakePoolRepo:
    def __init__(self, setting):
        self.setting = setting

    async def find(self, account_no, market):
        return self.setting


def pool_service(setting, owned=True):
    svc = PoolService.__new__(PoolService)
    svc.db = None
    svc.repo = _FakePoolRepo(setting)
    svc.account_repo = _FakeAccountRepo(owned)
    return svc


class ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_foreign_account_is_not_found(self):
        with self.assertRaises(NotFoundError):
            await pool_service(kr_setting(), owned=False).park("u", "ACC", "KR")

    async def test_requires_pool_on(self):
        for s in (None, kr_setting(use="N")):
            with self.assertRaises(BusinessRuleError):
                await pool_service(s).park("u", "ACC", "KR")
            with self.assertRaises(BusinessRuleError):
                await pool_service(s).get_parking("u", "ACC", "KR")

    async def test_park_response_shape_krw_int(self):
        result = parking.ParkResult(parking.PARKED, qty=3465, amount=34_650_000.7)
        with patch.object(parking, "park_idle_cash", AsyncMock(return_value=result)), \
             patch("app.domain.pool.service.get_redis", AsyncMock(return_value=FakeRedis())):
            out = await pool_service(kr_setting()).park("u", "ACC", "KR")
        self.assertEqual(out, {"STATUS": "parked", "QTY": 3465, "AMOUNT": 34_650_000, "MESSAGE": ""})

    async def test_us_amount_keeps_cents(self):
        us = PoolSetting.create("ACC", "US", use_yn="Y")
        result = parking.ParkResult(parking.PARKED, qty=10, amount=1005.456)
        with patch.object(parking, "park_idle_cash", AsyncMock(return_value=result)), \
             patch("app.domain.pool.service.get_redis", AsyncMock(return_value=FakeRedis())):
            out = await pool_service(us).park("u", "ACC", "US")
        self.assertEqual(out["AMOUNT"], 1005.46)

    async def test_get_parking_value_and_unreadable(self):
        with patch.object(parking, "fetch_park_holding", AsyncMock(return_value=(100, 10_250.0))):
            out = await pool_service(kr_setting()).get_parking("u", "ACC", "KR")
        self.assertEqual(out, {"PARK_CODE": "459580", "QTY": 100, "VALUE": 1_025_000})
        with patch.object(parking, "fetch_park_holding", AsyncMock(return_value=None)):
            out = await pool_service(kr_setting()).get_parking("u", "ACC", "KR")
        self.assertEqual((out["QTY"], out["VALUE"]), (None, None))


class _FakeDb:
    async def commit(self):
        pass

    async def rollback(self):
        pass


class _SavingRepo(_FakePoolRepo):
    async def save(self, setting):
        self.setting = setting
        return setting


class UpdateRequestShapeTest(unittest.IsolatedAsyncioTestCase):
    """앱이 실제로 보내는 요청 형태 — 남길 현금 비율만 저장하고, 파킹은 풀이 켜져 있으면 허용"""

    def service(self, setting=None):
        svc = pool_service(setting)
        svc.db = _FakeDb()
        svc.repo = _SavingRepo(setting)
        return svc

    async def put(self, svc, **fields):
        from app.domain.pool.schemas import PoolSettingUpdateRequest
        base = dict(ACCOUNT_NO="ACC", MARKET="KR", USE_YN="Y", PARK_RESERVE_PCT=0.05)
        return await svc.update_setting("u", PoolSettingUpdateRequest(**{**base, **fields}))

    async def test_pool_on_enables_parking_without_a_switch(self):
        svc = self.service()
        await self.put(svc)
        self.assertTrue(svc.repo.setting.parking_enabled())

    async def test_app_cannot_send_park_yn(self):
        """파킹 사용 스위치는 없앴다 — 보내면 거절한다"""
        from pydantic import ValidationError as PydanticValidationError
        from app.domain.pool.schemas import PoolSettingUpdateRequest
        with self.assertRaises(PydanticValidationError):
            PoolSettingUpdateRequest(ACCOUNT_NO="ACC", MARKET="KR", USE_YN="Y", PARK_YN="Y")

    async def test_request_without_reserve_keeps_it(self):
        from app.domain.pool.schemas import PoolSettingUpdateRequest
        svc = self.service()
        await self.put(svc, PARK_RESERVE_PCT=0.1)
        out = await svc.update_setting("u", PoolSettingUpdateRequest(ACCOUNT_NO="ACC", MARKET="KR", USE_YN="Y"))
        self.assertEqual(out["PARK_RESERVE_PCT"], Decimal("0.1"))

    async def test_app_cannot_send_etf_fields(self):
        """종목은 고정이라 요청 스키마에 없다 — 보내면 거절한다 (조용히 무시하지 않는다)"""
        from pydantic import ValidationError as PydanticValidationError
        from app.domain.pool.schemas import PoolSettingUpdateRequest
        for field in ("PARK_CODE", "PARK_EXCG"):
            with self.assertRaises(PydanticValidationError, msg=field):
                PoolSettingUpdateRequest(ACCOUNT_NO="ACC", MARKET="KR", USE_YN="Y", **{field: "005930"})


if __name__ == "__main__":
    unittest.main()
