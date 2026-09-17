"""
매핑 보유종목 포지션 편입 테스트

실행:
  PYTHONPATH=. python -m unittest tests.test_swing_mapped_adoption -v
  또는 (pytest 설치 시):
  pytest tests/test_swing_mapped_adoption.py -v

대상 불변식: SIGNAL 0(매수대기) + HOLD_QTY>0 은 성립할 수 없다.
  - 신규 진입으로 처리되면 transition_to_buy가 기존 수량/평단을 덮어써 포지션이 유실된다
  - 손절/익절은 has_position()(SIGNAL 1/2)에서만 평가되므로 방치되면 무방비 상태가 된다

강제 지점 2곳:
  1) SwingService.update_swing — 활성화(USE_YN N→Y) 시 증권사 실보유로 편입
  2) auto_swing_batch.process_single_swing — 사이클 진입 시 DB값으로 편입 (안전망)
"""
import unittest
from decimal import Decimal

from app.domain.swing import service as svc_mod
from app.domain.swing.entity import SwingTrade
from app.domain.swing.service import SwingService
from app.domain.swing.trading import auto_swing_batch as batch
from app.domain.swing.trading.strategies.single_ema_strategy import SingleEMAStrategy
from app.exceptions import ValidationError

from tests.test_swing_trade_scenarios import (
    FakeTradeService, Market, SwingScenarioBase,
)


def _make_swing(**over) -> SwingTrade:
    defaults = dict(
        SWING_ID=1, ACCOUNT_NO="12345678-01", MRKT_CODE="J", ST_CODE="005930",
        SWING_TYPE="S", USE_YN="N", SIGNAL=0,
        INIT_AMOUNT=Decimal(7_000_000), CUR_AMOUNT=Decimal(0),
        ENTRY_PRICE=Decimal(70_000), HOLD_QTY=100,
    )
    defaults.update(over)
    return SwingTrade(**defaults)


# ==================== Entity ====================

class AdoptPositionTest(unittest.TestCase):
    """adopt_position / clear_orphan_position 단위 검증"""

    def test_adopt_sets_position_state(self):
        s = _make_swing(SIGNAL=0, HOLD_QTY=0, ENTRY_PRICE=None)
        s.adopt_position(hold_qty=100, entry_price=70_000, current_price=75_000)

        self.assertEqual(s.SIGNAL, 1)
        self.assertTrue(s.has_position(), "편입 후 손절/익절 평가 대상이어야 함")
        self.assertEqual(s.HOLD_QTY, 100)
        self.assertEqual(float(s.ENTRY_PRICE), 70_000)
        self.assertEqual(float(s.PEAK_PRICE), 75_000)

    def test_peak_floors_at_entry_price(self):
        """급락 중 편입 — PEAK이 평단 아래로 잡히면 트레일링 익절 기준이 비정상적으로 낮아진다"""
        s = _make_swing(SIGNAL=0, HOLD_QTY=0, ENTRY_PRICE=None)
        s.adopt_position(hold_qty=100, entry_price=70_000, current_price=60_000)

        self.assertEqual(float(s.PEAK_PRICE), 70_000)

    def test_adopt_rejected_when_not_waiting(self):
        s = _make_swing(SIGNAL=1)
        with self.assertRaises(ValidationError):
            s.adopt_position(hold_qty=100, entry_price=70_000, current_price=70_000)

    def test_adopt_rejected_on_invalid_inputs(self):
        for qty, price in ((0, 70_000), (-1, 70_000), (100, 0), (100, None)):
            with self.subTest(qty=qty, price=price):
                s = _make_swing(SIGNAL=0)
                with self.assertRaises(ValidationError):
                    s.adopt_position(hold_qty=qty, entry_price=price, current_price=70_000)

    def test_clear_orphan_position(self):
        s = _make_swing(PEAK_PRICE=Decimal(80_000))
        s.clear_orphan_position()

        self.assertEqual(s.HOLD_QTY, 0)
        self.assertIsNone(s.ENTRY_PRICE)
        self.assertIsNone(s.PEAK_PRICE)
        self.assertEqual(s.SIGNAL, 0, "실보유 0주는 정상 매수대기로 남아야 함")


# ==================== Batch (안전망) ====================

class BatchOrphanPositionTest(SwingScenarioBase):
    """사이클 진입 시 SIGNAL=0 + HOLD_QTY>0 처리"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.notifications = []
        batch._fire_trade_notification = lambda *a, **k: self.notifications.append(a)

    def _make_orphan(self, cur_amount=0, entry_price=90.0):
        """매핑 등록분 재현: 보유 수량은 있으나 SIGNAL=0, 배정금 0"""
        self.swing.SIGNAL = 0
        self.swing.HOLD_QTY = 100
        self.swing.ENTRY_PRICE = Decimal(str(entry_price)) if entry_price else None
        self.swing.CUR_AMOUNT = Decimal(str(cur_amount))

    async def test_orphan_is_adopted_not_bought(self):
        self._make_orphan()
        await self.run_cycles(1)

        self.assertEqual(self.swing.SIGNAL, 1, "고아 포지션이 편입되지 않았습니다")
        self.assertEqual(self.swing.HOLD_QTY, 100, "보유 수량이 유실됨")
        self.assertEqual(float(self.swing.ENTRY_PRICE), 90.0, "평단이 덮어써짐")
        self.assertEqual(Market.buys(), [], "편입 대상에 신규 매수가 나갔습니다")
        self.assertEqual(FakeTradeService.of("B"), [], "체결 없이 매수 이력이 저장됨")

    async def test_no_buy_even_when_capital_allocated(self):
        """증상 B 재현 방지: INIT_AMOUNT 증액으로 CUR_AMOUNT>0이어도 신규 매수 금지"""
        self._make_orphan(cur_amount=3_000_000)
        await self.run_cycles(SingleEMAStrategy.CONSECUTIVE_REQUIRED)

        self.assertEqual(Market.buys(), [], "기존 포지션을 덮어쓰는 매수가 실행됨")
        self.assertEqual(self.swing.HOLD_QTY, 100, "보유 수량이 신규 체결 수량으로 덮어써짐")

    async def test_adoption_does_not_fire_buy_notification(self):
        """편입은 체결이 아니므로 '매수 완료' 푸시가 나가면 안 된다"""
        self._make_orphan()
        await self.run_cycles(1)

        self.assertEqual(self.swing.SIGNAL, 1)
        self.assertEqual(self.notifications, [], "편입이 매수 체결 알림으로 오인됨")

    async def test_skipped_when_entry_price_missing(self):
        """평단을 얻을 수 없으면 손절/익절 기준을 세울 수 없다 → 상태 변경 없이 스킵"""
        self._make_orphan(cur_amount=3_000_000, entry_price=None)
        await self.run_cycles(1)

        self.assertEqual(self.swing.SIGNAL, 0)
        self.assertEqual(self.swing.HOLD_QTY, 100)
        self.assertEqual(Market.orders, [], "편입 불가 상태에서 주문이 나갔습니다")

    # --- 편입 기준값은 DB가 아니라 증권사 ---

    async def test_adopts_broker_qty_over_stale_db_qty(self):
        """DB 스냅샷이 낡았을 때(매핑 후 사용자가 직접 일부 매도) 증권사 실보유로 편입한다.

        DB 수량으로 편입하면 보유하지 않은 40주까지 매도 주문에 실려
        매 사이클 거절되고 자가 회복되지 않는다.
        """
        self._make_orphan(entry_price=90.0)  # DB: 100주 @ 90
        self.broker_position = lambda: {"qty": 60, "avg_price": 88.0, "prpr": 101.0}

        await self.run_cycles(1)

        self.assertEqual(self.swing.SIGNAL, 1, "편입되지 않았습니다")
        self.assertEqual(self.swing.HOLD_QTY, 60, "낡은 DB 수량으로 편입됨")
        self.assertEqual(float(self.swing.ENTRY_PRICE), 88.0, "낡은 DB 평단으로 편입됨")
        self.assertEqual(Market.buys(), [])

    async def test_cleared_when_broker_holds_nothing(self):
        """사용자가 증권사에서 직접 전량 매도 → 없는 주식을 편입하지 않고 매수대기로 정리"""
        self._make_orphan(entry_price=90.0)
        self.broker_position = lambda: {"qty": 0, "avg_price": 0.0, "prpr": 101.0}

        await self.run_cycles(1)

        self.assertEqual(self.swing.SIGNAL, 0, "없는 포지션이 편입됨")
        self.assertEqual(self.swing.HOLD_QTY, 0, "잔여 수량이 정리되지 않음")
        self.assertIsNone(self.swing.ENTRY_PRICE)
        self.assertEqual(Market.sells(), [], "보유하지 않은 주식에 매도가 나갔습니다")

    async def test_skipped_when_broker_fetch_fails(self):
        """실보유 조회 실패 시 낡은 DB값으로 편입하지 않고 다음 사이클로 미룬다"""
        self._make_orphan(entry_price=90.0)
        self.broker_position = lambda: None

        await self.run_cycles(1)

        self.assertEqual(self.swing.SIGNAL, 0, "조회 실패인데 편입됨")
        self.assertEqual(self.swing.HOLD_QTY, 100, "상태가 변경됨")
        self.assertEqual(Market.orders, [], "편입 보류 상태에서 주문이 나갔습니다")

    async def test_orphan_without_indicator_cache_logs_error(self):
        """지표 캐시가 없으면 편입 전에 return된다 — SIGNAL=0이어도 무방비 포지션은 error로 알려야 한다"""
        self._make_orphan()
        self.redis.store.pop("indicators:AAPL")

        with self.assertLogs("app.domain.swing.trading.auto_swing_batch", level="ERROR") as cm:
            await self.run_cycles(1)

        self.assertTrue(
            any("손절/익절 평가 불가" in line for line in cm.output),
            f"무방비 포지션이 warning으로 묻혔습니다: {cm.output}"
        )
        self.assertEqual(Market.orders, [], "캐시 없이 주문이 나갔습니다")

    async def test_adopted_position_is_evaluated_same_cycle(self):
        """편입 직후 같은 사이클에서 손절이 평가되어야 한다 (다음 틱까지 무방비 금지)"""
        Market.reset(price=80.0)  # 평단 90 - ATR(2.0)x2 = 86 하회 → 손절선
        self._make_orphan(entry_price=90.0)
        await self.run_cycles(1)

        self.assertEqual(len(Market.sells()), 1, "편입 후 손절이 평가되지 않았습니다")
        self.assertEqual(Market.buys(), [])


# ==================== Service (활성화 시 편입) ====================

class FakeRepo:
    """repo.update의 Core bulk UPDATE(synchronize_session=False) 동작 재현

    DB에는 반영되지만 in-memory 인스턴스는 갱신되지 않는다.
    """
    def __init__(self, swing):
        self.swing = swing
        self.db_state = {}

    async def find_by_id(self, swing_id):
        return self.swing

    async def update(self, swing_id, data):
        self.db_state.update(data)
        return self.swing


class FakeDB:
    def __init__(self, repo):
        self.repo = repo
        self.commits = 0
        self.refreshed = False

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        raise AssertionError("update_swing 처리 중 rollback 되었습니다")

    async def refresh(self, obj):
        self.refreshed = True
        for k, v in self.repo.db_state.items():
            setattr(obj, k, v)


class FakeStockService:
    """데이터 적재 트리거 우회 (DATA_YN='Y' → READY)"""
    def __init__(self, db):
        pass

    async def get_stock_info(self, mrkt_code, st_code):
        return {"ST_CODE": st_code, "DATA_YN": "Y"}


class ActivateAdoptionTest(unittest.IsolatedAsyncioTestCase):
    """update_swing 활성화 시 포지션 편입"""

    async def asyncSetUp(self):
        self._orig_stock = svc_mod.StockService
        self._orig_balance = svc_mod.get_stock_balance
        svc_mod.StockService = FakeStockService

        self.swing = _make_swing(USE_YN="N", HOLD_QTY=50, ENTRY_PRICE=Decimal(70_000))
        self.repo = FakeRepo(self.swing)
        self.db = FakeDB(self.repo)

        self.service = SwingService.__new__(SwingService)
        self.service.db = self.db
        self.service.repo = self.repo

        # 자본 한도 검증 우회 (모의투자와 동일: 추적 불가)
        async def _no_capital_limit(*a, **kw):
            return {"total_capital": None, "allocated": 0,
                    "available_capital": None, "capital_tracking": False}
        self.service.get_available_capital = _no_capital_limit

    async def asyncTearDown(self):
        svc_mod.StockService = self._orig_stock
        svc_mod.get_stock_balance = self._orig_balance

    def _set_holdings(self, *items):
        async def _balance(user_id, db, account_no=None):
            # 편입은 스윙이 등록된 계좌 기준으로 조회해야 한다 (세션 계좌가 아님)
            self.seen_account = account_no
            return {"output1": list(items), "output2": {}}
        svc_mod.get_stock_balance = _balance

    def _fail_holdings(self):
        async def _balance(user_id, db, account_no=None):
            raise RuntimeError("KIS 조회 실패")
        svc_mod.get_stock_balance = _balance

    async def test_adopts_broker_quantity_not_db_snapshot(self):
        """DB 50주(매핑 시점) vs 실보유 150주 → 증권사 값이 단일 진실"""
        self._set_holdings({"pdno": "005930", "hldg_qty": "150",
                            "pchs_avg_pric": "68000", "prpr": "72000"})

        result = await self.service.update_swing(1, {"USE_YN": "Y"}, user_id="tester")

        self.assertEqual(self.swing.SIGNAL, 1)
        self.assertEqual(self.swing.HOLD_QTY, 150)
        self.assertEqual(float(self.swing.ENTRY_PRICE), 68_000)
        self.assertEqual(float(self.swing.PEAK_PRICE), 72_000)
        self.assertEqual(result["SIGNAL"], 1, "편입 결과가 응답에 반영되지 않음")
        self.assertEqual(result["HOLD_QTY"], 150, "편입 수량이 응답에 실리지 않음")

    async def test_clears_position_when_broker_has_none(self):
        """매핑 후 사용자가 증권사에서 직접 전량 매도한 경우"""
        self._set_holdings()  # 보유 목록 비어있음

        await self.service.update_swing(1, {"USE_YN": "Y"}, user_id="tester")

        self.assertEqual(self.swing.SIGNAL, 0, "실보유 0주는 매수대기로 남아야 함")
        self.assertEqual(self.swing.HOLD_QTY, 0)
        self.assertIsNone(self.swing.ENTRY_PRICE)

    async def test_activation_succeeds_when_broker_query_fails(self):
        """조회 실패로 활성화 자체를 막지 않는다 (fail-soft, 배치 가드가 안전망)"""
        self._fail_holdings()

        result = await self.service.update_swing(1, {"USE_YN": "Y"}, user_id="tester")

        self.assertEqual(result["USE_YN"], "Y", "조회 실패로 활성화가 막혔습니다")
        self.assertEqual(self.swing.SIGNAL, 0, "편입은 보류되어야 함")
        self.assertEqual(self.swing.HOLD_QTY, 50, "기존 수량은 유지되어야 함")

    async def test_invalid_broker_price_does_not_fail_activation(self):
        """증권사가 qty>0 + 평단 0을 반환 — 편입만 보류하고 활성화는 성공해야 한다

        예외를 올리면 활성화는 이미 commit된 상태라 '실패로 보이지만 실제로는 활성화됨'
        불일치가 생긴다. 다음 사이클에 배치 진입 가드가 DB값으로 재처리한다.
        """
        self._set_holdings({"pdno": "005930", "hldg_qty": "100",
                            "pchs_avg_pric": "0", "prpr": "0"})

        result = await self.service.update_swing(1, {"USE_YN": "Y"}, user_id="tester")

        self.assertEqual(result["USE_YN"], "Y", "편입 실패가 활성화를 막았습니다")
        self.assertEqual(self.swing.SIGNAL, 0, "편입은 보류되어야 함")
        self.assertEqual(self.swing.HOLD_QTY, 50, "기존 수량이 훼손됨")
        self.assertEqual(float(self.swing.ENTRY_PRICE), 70_000, "기존 평단이 훼손됨")

    async def test_no_adoption_when_no_position(self):
        """보유 수량이 없는 일반 스윙은 편입 대상이 아니다 (증권사 조회도 불필요)"""
        self.swing.HOLD_QTY = 0
        self.swing.ENTRY_PRICE = None
        self._fail_holdings()  # 호출되면 예외 → 조회 자체가 없어야 통과

        result = await self.service.update_swing(1, {"USE_YN": "Y"}, user_id="tester")

        self.assertEqual(self.swing.SIGNAL, 0)
        self.assertEqual(result["USE_YN"], "Y")

    async def test_response_reflects_committed_values(self):
        """FR-05: repo.update가 in-memory를 갱신하지 않으므로 refresh 없이는 변경 전 값이 응답된다"""
        self.swing.HOLD_QTY = 0
        self.swing.ENTRY_PRICE = None

        result = await self.service.update_swing(
            1, {"USE_YN": "Y", "INIT_AMOUNT": 9_000_000}, user_id="tester"
        )

        self.assertTrue(self.db.refreshed, "응답 전 refresh가 호출되지 않았습니다")
        self.assertEqual(result["USE_YN"], "Y", "활성화 응답이 변경 전 값을 반환함")
        self.assertEqual(int(result["INIT_AMOUNT"]), 9_000_000)
        self.assertEqual(result["DATA_STATUS"], "READY")


if __name__ == "__main__":
    unittest.main()


# ==================== 전량매도 알림 (범위 외 기존 결함 수정) ====================

class SellNotificationTest(SwingScenarioBase):
    """전량 매도 푸쉬 알림 발송 검증

    reset_cycle은 항상 SIGNAL=3으로 전이하므로, 알림 조건이 SIGNAL=0을 기대하면
    전량매도 알림이 조용히 누락된다. 실제 PushNotificationService까지 통과시켜 확인한다.
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # 기본 하네스는 _fire_trade_notification을 no-op으로 막으므로 실제 구현을 되살린다
        batch._fire_trade_notification = self._orig["notify"]

        self.pushes = []
        self._orig_push = batch.PushNotificationService.send_trade_notification

        async def _record(**kw):
            self.pushes.append(kw)

        batch.PushNotificationService.send_trade_notification = staticmethod(_record)

    async def asyncTearDown(self):
        batch.PushNotificationService.send_trade_notification = self._orig_push
        await super().asyncTearDown()

    async def _drain(self):
        """fire-and-forget 알림 태스크 완료 대기"""
        import asyncio
        for _ in range(3):
            await asyncio.sleep(0)

    def _of(self, keyword):
        return [p for p in self.pushes if keyword in p["reasons"][0]]

    async def test_full_sell_fires_notification(self):
        await self.enter_position()
        sold_qty = self.swing.HOLD_QTY
        await self._drain()
        self.pushes.clear()

        Market.move(80.0)  # 평단 대비 손절선 하회 → 전량 매도
        await self.run_cycles(1)
        await self._drain()

        self.assertEqual(self.swing.SIGNAL, 3, "전량 매도가 실행되지 않았습니다")
        sells = self._of("전량 매도 완료")
        self.assertEqual(len(sells), 1, f"전량매도 알림이 발송되지 않았습니다: {self.pushes}")
        self.assertEqual(sells[0]["qty"], sold_qty, "알림에 매도 수량이 0으로 나감")
        self.assertGreater(sells[0]["price"], 0, "알림에 체결가가 0으로 나감")

    async def test_partial_take_profit_reports_sold_qty(self):
        """1차 익절 알림의 수량은 '매도 수량'이어야 한다 (잔여 수량이 아님)"""
        await self.enter_position()
        entry, hold = self.entry_price(), self.swing.HOLD_QTY
        await self._drain()
        self.pushes.clear()

        target = entry * (1 + SingleEMAStrategy.PARTIAL_TAKE_PROFIT_PCT / 100)
        Market.move(target * 1.01)
        await self.run_cycles(1)
        await self._drain()

        self.assertEqual(self.swing.SIGNAL, 2, "1차 익절이 실행되지 않았습니다")
        sold = hold - self.swing.HOLD_QTY

        pushes = self._of("1차 익절")
        self.assertEqual(len(pushes), 1, f"1차 익절 알림 누락: {self.pushes}")
        self.assertEqual(pushes[0]["qty"], sold, "잔여 수량을 매도 수량으로 표기함")
        self.assertNotEqual(pushes[0]["qty"], self.swing.HOLD_QTY or -1,
                            "매도 수량과 잔여 수량이 구분되지 않음")
        self.assertAlmostEqual(pushes[0]["price"], Market.price, delta=0.5,
                               msg="매도 알림 단가가 체결가가 아닌 평단으로 나감")

    async def test_buy_notification_still_fires(self):
        """회귀: 매수 완료 알림은 기존대로 동작"""
        await self.enter_position()
        await self._drain()

        buys = self._of("매수 완료")
        self.assertEqual(len(buys), 1, f"매수 알림 회귀: {self.pushes}")
        self.assertEqual(buys[0]["qty"], self.swing.HOLD_QTY)
