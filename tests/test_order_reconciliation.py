"""
주문 결과 미상 추적 + 증권사 실보유 대조(기록 누락 복구) 테스트

배경 (2026-09-29 운영): 기존 보유 편입 종목(079550)이 청산 신호에 매도됐는데
TRADE_HISTORY 에 매도가 없고, 목록 손익은 원금 전액 손실로 표시됐다. 이후 매 사이클
매도가 '모의투자 잔고내역이 없습니다'로 거부됐다 — 증권사에서는 체결됐는데 주문 응답이
유실(유량초과 500 → 재시도 / 타임아웃 → 롤백)되어 DB 에 반영되지 않은 것.

고정하는 동작:
  1. http_client: 신규 주문(order=True)은 접수 여부를 모르는 실패를 재시도하지 않고
     OrderOutcomeUnknownError 로 올린다. 조회/정정취소는 기존 재시도 유지.
  2. 결과 미상 주문 → 체결 미확인(pending_order, order_no=None)으로 추적 →
     다음 사이클 체결내역(종목·구분·수량·시각)으로 찾아 기록 / 못 찾으면 미접수로 종료.
  3. 매도 거부 시 실보유 대조 → 누락 매도 기록 + CUR_AMOUNT 가산 + 사이클 종료.
     체결을 못 찾으면 이력 없이 정리, 조회 실패면 상태 유지.
  4. 매수 직전 실보유 확인 → 이미 보유면 신규 매수 대신 편입 (중복 매수 방지).

실행: pytest tests/test_order_reconciliation.py -v
"""
import asyncio
import datetime as dt
import json
import unittest
from decimal import Decimal
from unittest.mock import patch

import httpx

import app.external.http_client as hc
from app.exceptions import ExternalServiceError, OrderOutcomeUnknownError
from app.domain.swing.trading import auto_swing_batch as batch
from app.domain.swing.trading import order_executor as oe
from app.domain.swing.trading.strategies.single_ema_strategy import SingleEMAStrategy
from tests.test_swing_trade_scenarios import FakeTradeService, Market, SwingScenarioBase


# ==================== 1. http_client 분류 ====================

class _FakeClient:
    """handler(request, n) 가 돌려준 응답/예외를 그대로 내는 httpx 클라이언트 대체"""

    def __init__(self, handler):
        self.handler, self.calls = handler, 0

    async def request(self, method, url, **kwargs):
        self.calls += 1
        req = httpx.Request(method, url)
        resp = self.handler(req, self.calls)
        resp.request, resp.elapsed = req, dt.timedelta(0)
        return resp


_real_sleep = asyncio.sleep


async def _no_sleep(*_):
    await _real_sleep(0)


def _rate_limited(req, n):
    return httpx.Response(500, json={"msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."})


def _ok(req, n):
    return httpx.Response(200, json={"rt_cd": "0"})


class OrderFetchClassificationTest(unittest.IsolatedAsyncioTestCase):

    async def _fetch(self, handler, method="POST", **kw):
        client = _FakeClient(handler)
        with patch.object(hc, "get_client", return_value=client), \
                patch.object(hc.asyncio, "sleep", new=_no_sleep):
            try:
                return await hc.fetch(method, "https://kis/order", "KIS", **kw), client.calls
            except ExternalServiceError as e:
                return e, client.calls

    async def test_order_rate_limit_is_not_retried(self):
        """주문 유량초과: 재시도하면 이미 체결된 주문이 '잔고 없음'으로 거부돼 기록이 사라진다"""
        result, calls = await self._fetch(_rate_limited, order=True)
        self.assertIsInstance(result, OrderOutcomeUnknownError)
        self.assertEqual(calls, 1, "주문이 재전송됨 (중복 주문 위험)")

    async def test_order_5xx_and_read_timeout_are_outcome_unknown(self):
        def e500(req, n):
            return httpx.Response(500, json={"msg1": "조회 처리 중 오류"})

        def read_timeout(req, n):
            raise httpx.ReadTimeout("t", request=req)

        for handler in (e500, read_timeout):
            result, calls = await self._fetch(handler, order=True)
            self.assertIsInstance(result, OrderOutcomeUnknownError, handler.__name__)
            self.assertEqual(calls, 1, handler.__name__)

    async def test_order_4xx_is_definite_rejection(self):
        """4xx 는 확정 거부 — 결과 미상으로 올리면 거부된 주문을 30분간 추적하게 된다"""
        result, _ = await self._fetch(lambda r, n: httpx.Response(400, json={"msg1": "잘못된 요청"}), order=True)
        self.assertIs(type(result), ExternalServiceError)

    async def test_order_connect_timeout_is_retried(self):
        """연결 수립 실패는 서버에 도달하지 않았으므로 주문이라도 재시도가 안전하다"""
        def handler(req, n):
            if n == 1:
                raise httpx.ConnectTimeout("c", request=req)
            return _ok(req, n)

        result, calls = await self._fetch(handler, order=True)
        self.assertEqual(result["body"], {"rt_cd": "0"})
        self.assertEqual(calls, 2)

    async def test_non_order_requests_keep_rate_limit_retry(self):
        """조회(GET)·정정취소(order 미지정 POST)는 기존처럼 유량초과를 재시도한다"""
        def handler(req, n):
            return _rate_limited(req, n) if n == 1 else _ok(req, n)

        for method in ("GET", "POST"):
            result, calls = await self._fetch(handler, method=method)
            self.assertEqual(result["body"], {"rt_cd": "0"}, method)
            self.assertEqual(calls, 2, method)


# ==================== 2~4. 배치 시나리오 ====================

def _kst_now():
    return dt.datetime.now()


class Broker:
    """증권사 주문내역(체결내역) 대체 — find_executions 응답을 테스트가 구성한다"""
    orders = []          # [{order_no, ord_dt, ord_tmd, ord_qty, executed_qty, avg_price, executed_amt, side}]
    fail = False         # True 면 조회 실패(None)

    @classmethod
    def reset(cls):
        cls.orders, cls.fail = [], False

    @classmethod
    def add(cls, side, qty, price, at=None, order_no=None, executed_qty=None):
        at = at or _kst_now()
        executed = qty if executed_qty is None else executed_qty
        cls.orders.append({
            "side": side, "order_no": order_no or f"B{len(cls.orders) + 1:03d}",
            "ord_dt": at.strftime("%Y%m%d"), "ord_tmd": at.strftime("%H%M%S"),
            "ord_qty": qty, "executed_qty": executed, "avg_price": price,
            "executed_amt": executed * price,
        })

    @classmethod
    async def find_executions(cls, user_id, db, st_code, side, start_dt, end_dt, excg_cd="NAS", account_no=None):
        if cls.fail:
            return None
        return [{k: v for k, v in o.items() if k != "side"} for o in cls.orders
                if o["side"] == side and start_dt <= o["ord_dt"] <= end_dt]


class ReconcileScenarioBase(SwingScenarioBase):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        Broker.reset()
        self._orig_find = oe.foreign_api.find_executions
        oe.foreign_api.find_executions = Broker.find_executions

    async def asyncTearDown(self):
        oe.foreign_api.find_executions = self._orig_find
        await super().asyncTearDown()

    def hold_position(self, qty=50, entry=100.0, mod_dt_ago=dt.timedelta(hours=1)):
        """편입 완료 상태 (SIGNAL 1). MOD_DT 는 과거 — 그 뒤 체결이 누락 후보가 된다"""
        s = self.swing
        s.SIGNAL, s.HOLD_QTY = 1, qty
        s.ENTRY_PRICE = s.PEAK_PRICE = Decimal(str(entry))
        s.CUR_AMOUNT = Decimal(str(self.INIT_AMOUNT - qty * entry))
        s.MOD_DT = _kst_now() - mod_dt_ago


class OutcomeUnknownOrderTest(ReconcileScenarioBase):
    """2. 결과 미상 주문 → 체결내역 매칭"""

    def _place_then_lose_response(self, reach_broker: bool):
        """KIS 가 주문을 받았는데(또는 못 받았는데) 응답만 유실된 상황"""
        async def place(user_id, order, db, account_no=None):
            Market.orders.append({"dv": order.ord_dv, "qty": order.qty, "unpr": order.unpr,
                                  "account_no": account_no})
            if reach_broker:
                Broker.add(order.ord_dv, order.qty, order.unpr, order_no="0000012345")
            raise OrderOutcomeUnknownError("KIS", "초당 거래건수를 초과하였습니다.")
        oe.foreign_api.place_order_api = place

    async def test_lost_sell_response_is_recovered_next_cycle(self):
        """손절 매도 응답 유실 → 다음 사이클 체결내역에서 찾아 이력·CUR_AMOUNT·SIGNAL 반영"""
        await self.enter_position()
        hold, remain = self.swing.HOLD_QTY, self.cur_amount()
        self._place_then_lose_response(reach_broker=True)

        Market.move(self.entry_price() - 5.0)
        await self.run_cycles()

        self.assertEqual(self.swing.SIGNAL, 1, "결과 미상인데 상태가 바뀜")
        self.assertEqual(len(FakeTradeService.of("S")), 0)
        pending = json.loads(self.redis.store["pending_order:1"])
        self.assertIsNone(pending["order_no"])
        self.assertTrue(pending["placed_at"], "매칭 기준 주문시각이 없음")

        # 다음 사이클: 체결내역에 주문 존재 → 주문번호 매칭으로 기존 확인 흐름 합류
        oe.foreign_api.place_order_api = Market.place_order
        await self.run_cycles()

        sells = FakeTradeService.of("S")
        self.assertEqual(len(sells), 1, f"매도 이력 {len(sells)}건: {sells}")
        self.assertEqual(sells[0]["qty"], hold)
        self.assertEqual(self.swing.SIGNAL, 3, "사이클 종료(3)로 가야 함")
        self.assertAlmostEqual(self.cur_amount(), remain + sells[0]["amount"], delta=0.01)
        self.assertNotIn("pending_order:1", self.redis.store)
        self.assertEqual(len(Market.sells()), 1, "확인 중 매도가 재전송됨 (중복 주문)")
        self.assertEqual(Market.cancels, [], "주문번호 없이 취소를 시도함")

    async def test_lost_response_not_at_broker_expires_then_resumes(self):
        """접수되지 않은 주문은 한도까지 못 찾으면 추적을 끝내고 정상 평가로 돌아간다"""
        await self.enter_position()
        self._place_then_lose_response(reach_broker=False)
        Market.move(self.entry_price() - 5.0)
        await self.run_cycles()
        oe.foreign_api.place_order_api = Market.place_order

        await self.run_cycles(oe.SwingOrderExecutor.MAX_PENDING_ATTEMPTS)
        self.assertNotIn("pending_order:1", self.redis.store, "한도 후에도 추적 중")
        self.assertEqual(len(Market.sells()), 1, "추적 중 신규 매도가 나감")

        await self.run_cycles()  # 추적 종료 후 청산 재평가 → 정상 매도
        self.assertEqual(self.swing.SIGNAL, 3)
        self.assertEqual(len(FakeTradeService.of("S")), 1)

    async def test_matching_ignores_orders_before_placement(self):
        """같은 수량이라도 주문시각 이전 주문과는 매칭하지 않는다"""
        pending = {"type": "sell", "order_no": None, "ord_qty": 7, "mrkt_code": "NAS",
                   "placed_at": _kst_now().strftime("%Y%m%d%H%M%S")}
        Broker.add("sell", 7, 99.0, at=_kst_now() - dt.timedelta(minutes=10), order_no="OLD")
        self.assertIsNone(await oe.SwingOrderExecutor._find_unknown_order(
            "u1", "AAPL", "NAS", pending, self.db))

        Broker.add("sell", 7, 99.0, order_no="NEW")
        match = await oe.SwingOrderExecutor._find_unknown_order("u1", "AAPL", "NAS", pending, self.db)
        self.assertEqual(match["order_no"], "NEW")


class SellRejectedReconcileTest(ReconcileScenarioBase):
    """3. 매도 거부 → 실보유 대조 (운영 079550 재현)"""

    def reject_sells(self):
        async def reject(user_id, order, db, account_no=None):
            Market.orders.append({"dv": order.ord_dv, "qty": order.qty, "unpr": order.unpr,
                                  "account_no": account_no})
            return {"rt_cd": "1", "msg1": "모의투자 잔고내역이 없습니다."}
        oe.foreign_api.place_order_api = reject

    async def test_missing_sell_is_recorded_and_cycle_closed(self):
        self.hold_position(qty=50, entry=100.0)
        remain = self.cur_amount()
        sold_at = _kst_now() - dt.timedelta(minutes=30)
        Broker.add("sell", 50, 96.5, at=sold_at, order_no="0000077777")
        self.broker_position = lambda: {"qty": 0, "avg_price": 0.0, "prpr": 0.0}
        self.reject_sells()

        Market.move(95.0)  # 청산선 이탈
        await self.run_cycles()

        sells = FakeTradeService.of("S")
        self.assertEqual(len(sells), 1, f"누락 매도 복구 이력 {len(sells)}건")
        self.assertEqual((sells[0]["qty"], sells[0]["price"]), (50, 96.5), "체결내역 값이 아님")
        self.assertIn("체결 복구", sells[0]["reasons"])
        self.assertEqual(sells[0]["trade_date"], sold_at.replace(microsecond=0), "실제 체결 시각이 아님")
        self.assertAlmostEqual(self.cur_amount(), remain + 50 * 96.5, delta=0.01)
        self.assertEqual(self.swing.SIGNAL, 3)
        self.assertEqual(self.swing.HOLD_QTY, 0)

    async def test_already_recorded_fill_is_not_duplicated(self):
        """MOD_DT 이전 체결(이미 기록됨)은 누락 후보가 아니다"""
        self.hold_position(qty=50, mod_dt_ago=dt.timedelta(minutes=5))
        Broker.add("sell", 50, 96.5, at=_kst_now() - dt.timedelta(minutes=30))
        self.broker_position = lambda: {"qty": 0, "avg_price": 0.0, "prpr": 0.0}
        self.reject_sells()

        Market.move(95.0)
        await self.run_cycles()
        self.assertEqual(len(FakeTradeService.of("S")), 0, "이전 체결을 다시 기록함")
        self.assertEqual(self.swing.SIGNAL, 3, "실보유 0인데 사이클이 안 끝남")

    async def test_fill_not_found_closes_without_history(self):
        """체결내역에 없으면 가짜 체결을 만들지 않고 수량만 정리한다 (CUR_AMOUNT 미가산)"""
        self.hold_position(qty=50)
        remain = self.cur_amount()
        self.broker_position = lambda: {"qty": 0, "avg_price": 0.0, "prpr": 0.0}
        self.reject_sells()

        Market.move(95.0)
        await self.run_cycles()
        self.assertEqual(len(FakeTradeService.calls), 0)
        self.assertEqual(self.cur_amount(), remain)
        self.assertEqual(self.swing.SIGNAL, 3)

    async def test_lookup_failure_keeps_state(self):
        """체결내역 조회 실패 시에는 아무것도 바꾸지 않는다 (다음 사이클 재시도)"""
        self.hold_position(qty=50)
        self.broker_position = lambda: {"qty": 0, "avg_price": 0.0, "prpr": 0.0}
        Broker.fail = True
        self.reject_sells()

        Market.move(95.0)
        await self.run_cycles()
        self.assertEqual((self.swing.SIGNAL, self.swing.HOLD_QTY), (1, 50))

    async def test_rejection_with_matching_holdings_changes_nothing(self):
        """실보유가 DB 와 같으면 거부 사유가 다른 것 — 상태를 건드리지 않는다"""
        self.hold_position(qty=50)
        self.reject_sells()

        Market.move(95.0)
        await self.run_cycles()
        self.assertEqual((self.swing.SIGNAL, self.swing.HOLD_QTY), (1, 50))
        self.assertEqual(len(FakeTradeService.calls), 0)


class BuyGuardTest(ReconcileScenarioBase):
    """4. 매수 직전 실보유 확인 (중복 매수 방지)"""

    async def test_missing_buy_is_adopted_instead_of_rebuying(self):
        self.swing.MOD_DT = _kst_now() - dt.timedelta(hours=1)
        Broker.add("buy", 10, 100.0, at=_kst_now() - dt.timedelta(minutes=20))
        self.broker_position = lambda: {"qty": 10, "avg_price": 100.0, "prpr": 101.0}

        await self.run_cycles(SingleEMAStrategy.CONSECUTIVE_REQUIRED)

        self.assertEqual(Market.buys(), [], "이미 보유 중인데 매수가 나감 (중복 매수)")
        self.assertEqual((self.swing.SIGNAL, self.swing.HOLD_QTY), (1, 10))
        buys = FakeTradeService.of("B")
        self.assertEqual(len(buys), 1)
        self.assertIn("체결 복구", buys[0]["reasons"])
        self.assertAlmostEqual(self.cur_amount(), self.INIT_AMOUNT - 1000.0, delta=0.01)

    async def test_external_holding_is_adopted_without_deduction(self):
        """체결내역에 없는 보유(증권사 앱 매수)는 /list 자동등록처럼 자금 차감 없이 편입"""
        self.swing.MOD_DT = _kst_now() - dt.timedelta(hours=1)  # 조회는 되지만 체결이 없는 경로
        self.broker_position = lambda: {"qty": 10, "avg_price": 100.0, "prpr": 101.0}
        await self.run_cycles(SingleEMAStrategy.CONSECUTIVE_REQUIRED)

        self.assertEqual(Market.buys(), [])
        self.assertEqual(self.swing.SIGNAL, 1)
        self.assertEqual(self.cur_amount(), self.INIT_AMOUNT)
        self.assertEqual(FakeTradeService.calls, [])

    async def test_broker_check_failure_defers_buy(self):
        self.broker_position = lambda: None
        await self.run_cycles(SingleEMAStrategy.CONSECUTIVE_REQUIRED)
        self.assertEqual(Market.buys(), [], "실보유 미확인인데 매수가 나감")
        self.assertEqual(self.swing.SIGNAL, 0)


class StateSyncNotificationTest(ReconcileScenarioBase):
    """상태만 맞춘 경우는 매매 푸시(매수/매도 완료)를 보내지 않는다"""

    async def test_no_trade_push_on_state_sync(self):
        sent = []
        batch._fire_trade_notification = lambda *a, **k: sent.append(a)
        self.broker_position = lambda: {"qty": 10, "avg_price": 100.0, "prpr": 101.0}

        await self.run_cycles(SingleEMAStrategy.CONSECUTIVE_REQUIRED)
        self.assertEqual(self.swing.SIGNAL, 1)
        self.assertEqual(sent, [], "편입을 '매수 완료'로 푸시함")


if __name__ == "__main__":
    unittest.main()
