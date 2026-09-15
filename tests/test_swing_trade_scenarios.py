"""
스윙 매매 시나리오 통합 테스트 — 매수/매도 전 흐름 검증

실행:
  PYTHONPATH=. python -m unittest tests.test_swing_trade_scenarios -v
  또는 (pytest 설치 시):
  pytest tests/test_swing_trade_scenarios.py -v

대상: app/domain/swing/trading/auto_swing_batch.py 의 process_single_swing
  - 실제 엔티티(SwingTrade)와 실제 전략(SingleEMAStrategy)을 그대로 사용하고,
    DB/Redis/KIS API만 가짜로 대체해 사이클 단위로 돌린다.
  - 검증 대상: SIGNAL 상태 전이, HOLD_QTY/ENTRY_PRICE/PEAK_PRICE,
    CUR_AMOUNT(다음 매수 수량을 결정하는 값), TRADE_HISTORY 저장 건수/금액,
    분할 체결 Redis 상태, 부분 체결 잔량 취소

주의: TRADE_HISTORY 저장은 order_executor 한 곳에서만 이뤄져야 한다.
      배치가 중복 저장하면 실현손익이 왜곡되므로 건수를 함께 검증한다.
"""
import json
import unittest
from decimal import Decimal

from app.domain.swing.entity import SwingTrade
from app.domain.swing.trading import auto_swing_batch as batch
from app.domain.swing.trading import order_executor as oe
from app.domain.swing.trading.strategies.single_ema_strategy import SingleEMAStrategy


# ==================== 가짜 인프라 ====================

class FakeTradeService:
    """TRADE_HISTORY 저장 대체 — 호출 내역을 클래스 변수에 모은다"""
    calls = []

    def __init__(self, db):
        pass

    async def record_trade(self, swing_id, trade_type, order_result, reasons=None, mrkt_code=""):
        FakeTradeService.calls.append({
            "type": trade_type,
            "mrkt_code": mrkt_code,
            "qty": order_result.get("qty", 0),
            "price": order_result.get("avg_price", 0),
            "amount": order_result.get("amount", 0),
            "reasons": reasons,
        })
        return {}

    @classmethod
    def of(cls, trade_type):
        return [c for c in cls.calls if c["type"] == trade_type]


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


class FakeDB:
    async def flush(self):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        raise AssertionError("사이클 처리 중 예외가 발생해 rollback 되었습니다")

    async def close(self):
        pass


class Row:
    """SWING_TRADE 조인 결과 대체"""
    SWING_ID, ST_CODE, USER_ID, SWING_TYPE = 1, "AAPL", "u1", "S"


class Market:
    """한 사이클의 시세와 체결 응답을 제어"""
    price = 101.0
    volume = 150_000
    high = 101.5
    low = 99.5
    fill_ratio = 1.0          # 주문 수량 대비 체결 비율 (부분 체결 재현)
    orders = []
    cancels = []
    seen_accounts = set()     # API 계층에 전달된 계좌번호 (계좌 바인딩 검증용)

    @classmethod
    def reset(cls, price=101.0, volume=150_000, fill_ratio=1.0):
        cls.price, cls.volume, cls.fill_ratio = price, volume, fill_ratio
        cls.high, cls.low = price + 0.5, price - 1.5
        cls.orders, cls.cancels = [], []
        cls.seen_accounts = set()

    @classmethod
    def move(cls, price, volume=None):
        cls.price = price
        cls.high = max(cls.high, price + 0.5)
        cls.low = price - 0.5
        if volume is not None:
            cls.volume = volume

    # --- KIS API 대체 ---
    @classmethod
    async def quote(cls, *a, **k):
        return {"ask": round(cls.price + 0.02, 2), "bid": round(cls.price - 0.02, 2)}

    @classmethod
    async def place_order(cls, user_id, order, db, account_no=None):
        cls.seen_accounts.add(account_no)
        cls.orders.append({"dv": order.ord_dv, "qty": order.qty, "unpr": order.unpr,
                           "account_no": account_no})
        return {"rt_cd": "0", "output": {"ODNO": f"O{len(cls.orders):03d}",
                                         "KRX_FWDG_ORD_ORGNO": "91252"}}

    @classmethod
    async def execution(cls, user_id, order_no, db, **k):
        last = cls.orders[-1]
        qty = int(last["qty"] * cls.fill_ratio)
        return {"order_no": order_no, "executed_qty": qty, "avg_price": last["unpr"],
                "executed_amt": qty * last["unpr"], "remaining_qty": last["qty"] - qty}

    @classmethod
    async def cancel(cls, user_id, order, db, account_no=None):
        cls.seen_accounts.add(account_no)
        cls.cancels.append(order.orgn_odno)
        return {"rt_cd": "0"}

    @classmethod
    async def price_detail(cls, user_id, code, db, excd="NAS", account_no=None):
        cls.seen_accounts.add(account_no)
        return {"last": str(cls.price), "high": str(cls.high), "low": str(cls.low),
                "tvol": str(cls.volume), "base": "100.0"}

    @classmethod
    def sells(cls):
        return [o for o in cls.orders if o["dv"] == "sell"]

    @classmethod
    def buys(cls):
        return [o for o in cls.orders if o["dv"] == "buy"]


def make_indicator_cache(**over):
    """전일 완성봉 지표 캐시 (매수 게이트를 통과하는 값)"""
    cache = {
        "ema20": 100.0, "adx": 30.0, "plus_dm14": 8.0, "minus_dm14": 2.0,
        "atr": 2.0, "obv": 3_000_000.0, "obv_recent_diffs": [50_000] * 13,
        "obv_ema": 2_800_000.0, "avg_vol20": 100_000.0,
        "close": 100.0, "open": 99.5, "high": 100.5, "low": 99.0, "obv_z": 1.0,
        "date": "20260814", "avg_daily_amount": 100_000_000.0,
    }
    cache.update(over)
    return cache


async def _coro(value):
    return value


# ==================== 테스트 ====================

class SwingScenarioBase(unittest.IsolatedAsyncioTestCase):
    INIT_AMOUNT = 100_000

    async def asyncSetUp(self):
        import app.domain.trade_history as th

        self._orig = {
            "Database": batch.Database, "SwingService": batch.SwingService,
            "price": batch.foreign_api.get_inquire_price,
            "market_open": batch.is_market_open,
            "notify": batch._fire_trade_notification,
            "th_service": th.TradeHistoryService,
            "batch_th": getattr(batch, "TradeHistoryService", None),
            "quote": oe.foreign_api.get_best_quote,
            "place": oe.foreign_api.place_order_api,
            "exec": oe.foreign_api.check_order_execution,
            "cancel": oe.foreign_api.modify_or_cancel_order_api,
        }

        FakeTradeService.calls = []
        Market.reset()

        self.swing = SwingTrade(
            SWING_ID=1, ACCOUNT_NO="12345678-01", MRKT_CODE="NAS", ST_CODE="AAPL",
            INIT_AMOUNT=Decimal(str(self.INIT_AMOUNT)),
            CUR_AMOUNT=Decimal(str(self.INIT_AMOUNT)),
            SWING_TYPE="S", SIGNAL=0, HOLD_QTY=0, USE_YN="Y",
        )
        self.redis = FakeRedis()
        self.redis.store["indicators:AAPL"] = json.dumps(make_indicator_cache())
        self.db = FakeDB()

        swing, db = self.swing, self.db
        th.TradeHistoryService = FakeTradeService
        batch.TradeHistoryService = FakeTradeService
        batch.Database = type("D", (), {"get_session": staticmethod(lambda: _coro(db))})
        batch.SwingService = lambda _db: type("Svc", (), {
            "db": _db,
            "repo": type("R", (), {"find_by_id": staticmethod(lambda _id: _coro(swing))})(),
        })()
        batch.foreign_api.get_inquire_price = Market.price_detail
        batch.is_market_open = lambda *a, **k: True
        batch._fire_trade_notification = lambda *a, **k: None
        oe.foreign_api.get_best_quote = Market.quote
        oe.foreign_api.place_order_api = Market.place_order
        oe.foreign_api.check_order_execution = Market.execution
        oe.foreign_api.modify_or_cancel_order_api = Market.cancel

    async def asyncTearDown(self):
        import app.domain.trade_history as th

        batch.Database = self._orig["Database"]
        batch.SwingService = self._orig["SwingService"]
        batch.foreign_api.get_inquire_price = self._orig["price"]
        batch.is_market_open = self._orig["market_open"]
        batch._fire_trade_notification = self._orig["notify"]
        th.TradeHistoryService = self._orig["th_service"]
        if self._orig["batch_th"] is not None:
            batch.TradeHistoryService = self._orig["batch_th"]
        oe.foreign_api.get_best_quote = self._orig["quote"]
        oe.foreign_api.place_order_api = self._orig["place"]
        oe.foreign_api.check_order_execution = self._orig["exec"]
        oe.foreign_api.modify_or_cancel_order_api = self._orig["cancel"]

    # --- 헬퍼 ---
    async def run_cycles(self, n=1):
        for _ in range(n):
            await batch.process_single_swing(Row(), self.redis)

    async def enter_position(self):
        """매수 신호 연속 확인 → 진입"""
        await self.run_cycles(SingleEMAStrategy.CONSECUTIVE_REQUIRED)
        self.assertEqual(self.swing.SIGNAL, 1, "매수가 체결되지 않았습니다")

    def cur_amount(self):
        return float(self.swing.CUR_AMOUNT)

    def entry_price(self):
        return float(self.swing.ENTRY_PRICE) if self.swing.ENTRY_PRICE else 0


class SwingTradeScenarioTest(SwingScenarioBase):
    """정상 흐름 — 매수/매도 사이클"""

    # ==================== 매수 ====================

    async def test_buy_single_fill(self):
        """매수 신호 → 단일 체결: 상태/이력/가용금액이 모두 맞는가"""
        await self.enter_position()

        self.assertEqual(len(Market.buys()), 1, "매수 주문이 1회가 아님")
        self.assertGreater(self.swing.HOLD_QTY, 0)
        self.assertGreater(self.entry_price(), 0)
        self.assertGreater(float(self.swing.PEAK_PRICE), 0, "PEAK 미설정 → 트레일링 익절 불가")

        # 체결 이력은 executor 한 곳에서만 저장되어야 한다 (중복 저장 시 실현손익 왜곡)
        buys = FakeTradeService.of("B")
        self.assertEqual(len(buys), 1, f"매수 1건인데 이력 {len(buys)}건: {buys}")
        self.assertEqual(buys[0]["qty"], self.swing.HOLD_QTY)
        self.assertGreater(buys[0]["price"], 0, "이력에 체결가가 0으로 저장됨")

        # 가용금액은 체결가 기준으로 차감되어야 한다 (다음 매수 수량을 결정하는 값)
        spent = self.swing.HOLD_QTY * self.entry_price()
        self.assertAlmostEqual(self.cur_amount(), self.INIT_AMOUNT - spent, delta=1)

    async def test_buy_order_price_uses_ask(self):
        """해외 매수는 매도1호가 지정가로 나가야 한다"""
        await self.enter_position()
        quote = await Market.quote()
        self.assertEqual(Market.buys()[0]["unpr"], quote["ask"])

    # ==================== 매도 ====================

    async def test_stop_loss_full_sell(self):
        """손절 → 전량 매도 → 쿨다운 진입"""
        await self.enter_position()
        entry, hold = self.entry_price(), self.swing.HOLD_QTY
        remain = self.cur_amount()

        Market.move(entry - 5.0)           # 손절선(entry - ATR×2) 아래로
        await self.run_cycles()

        self.assertEqual(len(Market.sells()), 1)
        self.assertEqual(Market.sells()[0]["qty"], hold, "전량 매도가 아님")
        self.assertEqual(self.swing.SIGNAL, 3, "손절 후 쿨다운(3)으로 가야 함")
        self.assertEqual(self.swing.HOLD_QTY, 0)
        self.assertIsNone(self.swing.ENTRY_PRICE, "손절 후 평단가 미초기화")

        sells = FakeTradeService.of("S")
        self.assertEqual(len(sells), 1, f"매도 1건인데 이력 {len(sells)}건: {sells}")
        self.assertGreater(sells[0]["price"], 0, "매도 이력 체결가 0 → 실현손익 왜곡")

        # 매도 대금은 체결가(매수1호가) 기준으로 가산되어야 한다
        filled = Market.sells()[0]["unpr"]
        self.assertAlmostEqual(self.cur_amount(), remain + filled * hold, delta=0.01)

    async def test_partial_take_profit_then_exit_line(self):
        """부분 익절(목표 수익률) → 청산선 이탈 시 잔량 전량"""
        await self.enter_position()
        entry, hold = self.entry_price(), self.swing.HOLD_QTY

        # 목표 수익률 미달 구간에서는 절반 매도가 나가지 않는다
        Market.move(entry * 1.2)
        await self.run_cycles()
        self.assertEqual(len(Market.sells()), 0, "목표 수익률 전에 부분 익절이 나감")
        self.assertEqual(self.swing.SIGNAL, 1)

        # 목표 수익률(+PARTIAL_TAKE_PROFIT_PCT) 도달 → 절반 매도
        target = entry * (1 + SingleEMAStrategy.PARTIAL_TAKE_PROFIT_PCT / 100)
        Market.move(target * 1.01)
        await self.run_cycles()

        self.assertEqual(self.swing.SIGNAL, 2, "부분 익절 후 SIGNAL 2가 아님")
        self.assertEqual(self.swing.HOLD_QTY,
                         hold - int(hold * SingleEMAStrategy.FIRST_PROFIT_TAKE_RATIO))
        self.assertAlmostEqual(self.entry_price(), entry, delta=0.01,
                               msg="부분 익절 후 평단가가 바뀜")
        self.assertEqual(len(FakeTradeService.of("S")), 1)

        # 청산선 이탈 → 잔량 전량 (이익 30% 이상이라 청산선은 최소 평단가 위)
        Market.move(entry * 0.99)
        await self.run_cycles()

        self.assertEqual(self.swing.SIGNAL, 3, "청산선 이탈인데 사이클이 종료되지 않음")
        self.assertEqual(self.swing.HOLD_QTY, 0)

    async def test_exit_line_protects_breakeven(self):
        """본전 확보 이익률 도달 후 되돌리면 본전 부근에서 청산된다"""
        await self.enter_position()
        entry = self.entry_price()
        be = SingleEMAStrategy.BREAKEVEN_ACTIVATE_PCT

        # 본전 확보 단계 진입 (임계값보다 조금 위)
        Market.move(entry * (1 + (be + 5) / 100))
        await self.run_cycles()
        self.assertEqual(len(Market.sells()), 0, "이익 구간인데 청산됨")

        Market.move(entry * 0.99)          # 본전 아래로 되돌림 → 청산
        await self.run_cycles()
        self.assertEqual(len(Market.sells()), 1, "본전 확보 후 되돌렸는데 청산되지 않음")
        self.assertEqual(self.swing.SIGNAL, 3)

    async def test_cooldown_transition(self):
        """쿨다운 3 → 4 → 0 (수급 이탈 확인 후 재유입 확인)"""
        self.swing.SIGNAL = 3
        self.redis.store["indicators:AAPL"] = json.dumps(
            make_indicator_cache(obv_recent_diffs=[-80_000] * 13))
        Market.move(95.0, volume=200_000)
        await self.run_cycles()
        self.assertEqual(self.swing.SIGNAL, 4, "수급 이탈이 확인되지 않음")

        self.redis.store["indicators:AAPL"] = json.dumps(make_indicator_cache())
        Market.move(101.0)
        await self.run_cycles()
        self.assertEqual(self.swing.SIGNAL, 0, "수급 재유입이 확인되지 않음")

    # ==================== 분할 체결 ====================

    async def test_partial_buy_twap(self):
        """일거래대금이 작아 분할되는 경우 — 여러 사이클에 걸쳐 완료"""
        self.redis.store["indicators:AAPL"] = json.dumps(
            make_indicator_cache(avg_daily_amount=4_000_000.0))

        await self.run_cycles(SingleEMAStrategy.CONSECUTIVE_REQUIRED)
        self.assertIn("partial_exec:1", self.redis.store, "분할 상태가 저장되지 않음")
        self.assertEqual(self.swing.SIGNAL, 0, "분할 완료 전인데 SIGNAL이 바뀜")
        self.assertGreater(self.swing.HOLD_QTY, 0, "첫 chunk 체결분이 반영되지 않음")
        self.assertEqual(len(FakeTradeService.of("B")), 1, "첫 chunk 이력이 누락됨")

        for _ in range(6):
            if "partial_exec:1" not in self.redis.store:
                break
            await self.run_cycles()

        self.assertNotIn("partial_exec:1", self.redis.store, "분할 상태가 정리되지 않음")
        self.assertEqual(self.swing.SIGNAL, 1)
        self.assertEqual(self.swing.HOLD_QTY, sum(o["qty"] for o in Market.buys()),
                         "보유 수량이 주문 합계와 다름")

        invested = sum(t["amount"] for t in FakeTradeService.of("B"))
        self.assertAlmostEqual(self.cur_amount(), self.INIT_AMOUNT - invested, delta=2,
                               msg="분할 매수 후 가용금액이 이력 합계와 어긋남")

    async def test_partial_fill_cancels_remainder(self):
        """부분 체결(40%) — 잔량을 취소하고 체결분만 반영"""
        Market.fill_ratio = 0.4
        await self.run_cycles(SingleEMAStrategy.CONSECUTIVE_REQUIRED)

        ordered = Market.buys()[-1]["qty"]
        self.assertEqual(len(Market.cancels), 1, "부분 체결인데 잔량 취소가 없음")
        self.assertEqual(self.swing.HOLD_QTY, int(ordered * 0.4))
        for t in FakeTradeService.of("B"):
            self.assertEqual(t["qty"], int(ordered * 0.4), "이력 수량이 체결분과 다름")

    async def test_stop_loss_during_partial_buy_is_deferred(self):
        """분할 매수 중 급락 — 1사이클은 매수 중단, 매도는 다음 사이클 (의도된 동작)"""
        self.redis.store["indicators:AAPL"] = json.dumps(
            make_indicator_cache(avg_daily_amount=4_000_000.0))
        await self.run_cycles(SingleEMAStrategy.CONSECUTIVE_REQUIRED)
        held = self.swing.HOLD_QTY
        entry = self.entry_price()

        Market.move(85.0)                  # 손절선 한참 아래
        await self.run_cycles()

        self.assertEqual(len(Market.sells()), 0, "1사이클에는 매수 중단만 해야 함")
        self.assertEqual(self.swing.SIGNAL, 1, "매수 중단 후 보유 상태로 전환되어야 함")
        self.assertEqual(self.swing.HOLD_QTY, held)
        # PEAK는 평단가를 하한으로 잡아야 한다 (급락가로 잡히면 익절 기준이 무너짐)
        self.assertGreaterEqual(float(self.swing.PEAK_PRICE), entry)

        await self.run_cycles()
        self.assertEqual(len(Market.sells()), 1, "다음 사이클에 손절되지 않음")
        self.assertEqual(self.swing.SIGNAL, 3)
        self.assertEqual(self.swing.HOLD_QTY, 0)

    # ==================== 장 시간 가드 ====================

    async def test_no_order_outside_market_hours(self):
        """정규장 시간이 아니면 매매 배치가 실행되지 않는다"""
        batch.is_market_open = lambda *a, **k: False
        settings = batch.get_settings()
        original = settings.ALLOW_OFFHOURS_TRADING
        settings.ALLOW_OFFHOURS_TRADING = False
        try:
            await batch.us_trade_job()
            await batch.trade_job()
        finally:
            settings.ALLOW_OFFHOURS_TRADING = original
        self.assertEqual(len(Market.orders), 0, "장 시간 밖인데 주문이 나감")


if __name__ == "__main__":
    unittest.main(verbosity=2)

class AccountBindingTest(SwingScenarioBase):
    """배치는 세션이 아니라 스윙이 등록된 계좌로 시세/주문을 낸다

    Redis 세션의 ACCOUNT_NO는 '사용자가 앱에서 마지막으로 고른 계좌'라
    SWING_TRADE.ACCOUNT_NO와 다를 수 있다. 세션을 따라가면 실전 계좌로 등록한
    스윙이 모의 계좌에 주문되고, 실전 포지션은 청산되지 않은 채 남는다.
    """

    async def test_quote_uses_swing_account(self):
        """시세 조회가 스윙 계좌로 나간다"""
        await self.run_cycles(1)
        self.assertEqual(
            Market.seen_accounts, {self.swing.ACCOUNT_NO},
            f"시세 조회에 쓰인 계좌: {Market.seen_accounts}",
        )

    async def test_order_uses_swing_account(self):
        """주문이 스윙 계좌로 나간다 — 세션 계좌가 아님"""
        await self.enter_position()

        buys = Market.buys()
        self.assertTrue(buys, "매수 주문이 없습니다")
        for order in buys:
            self.assertEqual(
                order["account_no"], self.swing.ACCOUNT_NO,
                "주문이 스윙 계좌가 아닌 계좌로 나갔습니다",
            )

    async def test_no_call_leaks_without_account(self):
        """계좌 없이 호출되는 경로가 남아 있지 않다 (None이 섞이면 세션 폴백을 탄다)"""
        await self.enter_position()
        self.assertNotIn(
            None, Market.seen_accounts,
            "계좌 없이 나간 호출이 있습니다 → 세션 계좌로 폴백될 수 있음",
        )


class SwingTradeEdgeCaseTest(SwingScenarioBase):
    """엣지 케이스 — 실전에서 드물게 발생하지만 상태가 고착될 수 있는 경로"""

    async def test_take_profit_with_odd_single_share(self):
        """보유 1주에서 부분 익절 신호 — 절반이 0주라 매도 불가 상태가 되는가"""
        await self.enter_position()
        self.swing.HOLD_QTY = 1
        entry = self.entry_price()

        target = entry * (1 + SingleEMAStrategy.PARTIAL_TAKE_PROFIT_PCT / 100)
        Market.move(target * 1.01)
        await self.run_cycles()

        self.assertTrue(
            len(Market.sells()) > 0 or self.swing.SIGNAL != 1,
            f"보유 1주에서 익절 신호가 났으나 매도도 상태 전이도 없음 "
            f"(SIGNAL={self.swing.SIGNAL}, HOLD={self.swing.HOLD_QTY}) → 매 사이클 반복"
        )

    async def test_exit_line_after_partial_take_profit(self):
        """부분 익절 후(SIGNAL 2)에도 청산선이 잔량을 보호한다"""
        await self.enter_position()
        entry = self.entry_price()
        self.swing.SIGNAL = 2
        self.swing.HOLD_QTY = max(1, self.swing.HOLD_QTY // 2)
        self.swing.PEAK_PRICE = Decimal(str(round(entry * 1.5, 2)))   # 고점 +50%

        # 청산선 아래로 → 잔량 청산 (이익 30% 이상 → 청산선 ≥ 평단가)
        Market.move(entry * 0.99)
        await self.run_cycles()

        self.assertEqual(len(Market.sells()), 1, "SIGNAL 2에서 청산선 이탈인데 매도가 없음")
        self.assertEqual(self.swing.SIGNAL, 3)

    async def test_entry_consecutive_resets_on_filter(self):
        """공통 필터(급등)에 걸리면 연속 카운트가 리셋되어 매수가 안 나가야 한다"""
        await self.run_cycles(1)                    # 1회차 신호 적립
        Market.move(108.0)                          # 전일 대비 8% 급등 → MAX_SURGE_RATIO(5%) 초과
        await self.run_cycles(1)
        self.assertEqual(len(Market.buys()), 0, "급등 필터를 뚫고 매수가 나감")

        Market.move(101.0)                          # 정상 복귀 — 연속 카운트는 처음부터
        await self.run_cycles(1)
        self.assertEqual(len(Market.buys()), 0,
                         "필터 후 1회 만에 매수 (연속성 리셋이 동작하지 않음)")

    async def test_no_order_when_capital_insufficient(self):
        """가용금액이 1주 값도 안 되면 주문하지 않는다"""
        self.swing.CUR_AMOUNT = Decimal("50")       # 현재가 101 → 1주도 불가
        await self.run_cycles(SingleEMAStrategy.CONSECUTIVE_REQUIRED)
        self.assertEqual(len(Market.orders), 0, "자금 부족인데 주문이 나감")
        self.assertEqual(self.swing.SIGNAL, 0)

    async def test_order_rejected_keeps_state_for_retry(self):
        """주문이 거부되면 상태를 바꾸지 않고 다음 사이클에 재시도한다"""
        async def reject(user_id, order, db, account_no=None):
            Market.orders.append({"dv": order.ord_dv, "qty": order.qty, "unpr": order.unpr,
                                  "account_no": account_no})
            return {"rt_cd": "1", "msg1": "주문가능금액 부족"}

        oe.foreign_api.place_order_api = reject
        await self.run_cycles(SingleEMAStrategy.CONSECUTIVE_REQUIRED)

        self.assertEqual(self.swing.SIGNAL, 0, "주문 거부인데 상태가 바뀜")
        self.assertEqual(self.swing.HOLD_QTY, 0)
        self.assertEqual(len(FakeTradeService.calls), 0, "체결되지 않았는데 이력이 저장됨")

        oe.foreign_api.place_order_api = Market.place_order
        await self.run_cycles(1)
        self.assertEqual(self.swing.SIGNAL, 1, "거부 후 다음 사이클에 재시도되지 않음")


class ExitLineTest(unittest.TestCase):
    """청산선 계산 — 3단계가 순서대로 올라가고 내려가지 않는가"""

    ENTRY = 100.0
    ATR = 3.0

    def line(self, peak):
        return SingleEMAStrategy.calculate_exit_line(self.ENTRY, peak, self.ATR)

    def test_stage1_initial_stop(self):
        """진입 직후: max(진입가 − ATR×2, 진입가 × 0.93)"""
        expected = max(self.ENTRY - self.ATR * SingleEMAStrategy.ATR_MULTIPLIER,
                       self.ENTRY * (1 - SingleEMAStrategy.MAX_STOP_LOSS_PCT / 100))
        self.assertAlmostEqual(self.line(self.ENTRY), expected, places=6)
        self.assertLess(self.line(self.ENTRY), self.ENTRY, "초기 청산선이 평단가 위에 있음")

    def test_stage2_breakeven(self):
        """이익 10% 도달 → 청산선이 평단가로 상향 (손실 0 보장)"""
        below = self.ENTRY * (1 + SingleEMAStrategy.BREAKEVEN_ACTIVATE_PCT / 100 - 0.01)
        at = self.ENTRY * (1 + SingleEMAStrategy.BREAKEVEN_ACTIVATE_PCT / 100)
        self.assertLess(self.line(below), self.ENTRY, "본전 단계 전인데 청산선이 올라감")
        self.assertAlmostEqual(self.line(at), self.ENTRY, places=6)

    def test_stage3_trailing(self):
        """이익 30% 도달 → 고점 − ATR×3 추종"""
        peak = self.ENTRY * (1 + SingleEMAStrategy.TRAILING_ACTIVATE_PCT / 100)
        expected = peak - self.ATR * SingleEMAStrategy.TRAILING_STOP_ATR_MULT
        self.assertAlmostEqual(self.line(peak), max(expected, self.ENTRY), places=6)

        higher = self.ENTRY * 2
        self.assertAlmostEqual(self.line(higher),
                               higher - self.ATR * SingleEMAStrategy.TRAILING_STOP_ATR_MULT,
                               places=6)

    def test_line_never_decreases_with_peak(self):
        """고점이 올라가면 청산선도 올라가기만 한다 (래칫)"""
        prev = 0
        for pct in range(0, 200, 5):
            line = self.line(self.ENTRY * (1 + pct / 100))
            self.assertGreaterEqual(line + 1e-9, prev, f"고점 +{pct}%에서 청산선이 내려감")
            prev = line

    def test_profit_locked_once_trailing(self):
        """트레일링 활성 후에는 청산돼도 이익이 남는다"""
        peak = self.ENTRY * 1.8
        self.assertGreater(self.line(peak), self.ENTRY, "이익 80%인데 청산선이 평단가 이하")

    def test_invalid_inputs(self):
        self.assertEqual(SingleEMAStrategy.calculate_exit_line(0, 100, 3), 0.0)
        # ATR이 없어도 최대 손절 하한은 유지된다
        self.assertAlmostEqual(
            SingleEMAStrategy.calculate_exit_line(100, 100, 0),
            100 * (1 - SingleEMAStrategy.MAX_STOP_LOSS_PCT / 100), places=6)
