"""
풀 할당기 테스트

실행:
  uv run pytest tests/test_pool_allocator.py -v

할당기는 실제 주문의 크기·순서·한도를 정한다. 실제 매수 함수는 가짜로 대체해 호출 여부와 수량을 확인한다.
숫자: 총자산 1억, 리스크 0.10%(=10만 원), 가격 10,000원·ATR 200 → 진입 시 청산선 9,600, 주당 손절폭 400
→ 리스크 수량 250주(250만 원 = 총자산 2.5%).
"""
import unittest
from decimal import Decimal

from app.domain.pool.entity import PoolSetting
from app.domain.swing import risk_sizing
from app.domain.swing.trading import pool_allocator as pa
from app.domain.swing.trading.pool_allocator import (
    BUY_ADOPTED, BUY_FAILED, BUY_FILLED, BUY_UNCONFIRMED, BuyOutcome, EntryCandidate, run_pool_entries,
)
from app.domain.swing.trading.pool_state import AccountSnapshot, PoolPosition

EQUITY = 100_000_000.0


def cand(code="A", price=10_000.0, atr=200.0, conviction=0.5, adv=1e12, swing_id=1):
    return EntryCandidate(swing_id=swing_id, user_id="u", account_no="acc", st_code=code, mrkt_code="J",
                          price=price, conviction=conviction, atr=atr, avg_daily_amount=adv)


# 이 파일의 숫자(250주 등)는 분산형 구성(0.10%·30종목·10%) 기준이다 — 기본값이 바뀌어도 흔들리지 않게 값을 명시한다.
# 현재 기본값(집중형)의 동작은 DefaultConfigTest 가 따로 확인한다.
BASE = dict(risk_pct="0.0010", max_positions=30, max_position_pct="0.10", max_open_risk_pct="0.08")


def setting(**kw):
    return PoolSetting.create("acc", "KR", use_yn="Y", **{**BASE, **kw})


class Recorder:
    """buy 대역 — 호출을 기록하고 기본은 요청 수량 전부 현재가에 체결"""

    def __init__(self, outcome=None):
        self.calls = []
        self.outcome = outcome

    async def __call__(self, c, qty):
        self.calls.append((c.st_code, qty))
        if callable(self.outcome):
            return self.outcome(c, qty)
        return self.outcome or BuyOutcome(BUY_FILLED, qty, c.price)


def snap(cash=EQUITY, equity=EQUITY):
    return AccountSnapshot("KR", equity=equity, cash=cash)


async def run(cands, buy=None, positions=(), s=None, snapshot=None, **setting_kw):
    buy = buy or Recorder()
    result = await run_pool_entries(s or setting(**setting_kw), cands, snapshot or snap(), positions, buy)
    return result, buy


class SizingTest(unittest.IsolatedAsyncioTestCase):
    async def test_quantity_is_risk_based(self):
        """총자산 1억 × 0.10% = 10만 원 ÷ 주당 손절폭 400 = 250주"""
        result, buy = await run([cand()])
        self.assertEqual(buy.calls, [("A", 250)])
        self.assertEqual(result.filled, 1)

    async def test_initial_stop_matches_live_exit_line(self):
        """리스크 계산의 청산선은 실전 청산 판정 함수와 같다 (진입 직후 값)"""
        self.assertEqual(pa.initial_stop(10_000.0, 200.0), 9_600.0)       # max(9300, 10000−400)
        self.assertEqual(pa.initial_stop(10_000.0, 1_000.0), 9_300.0)     # ATR 이 커도 최대 손절폭 7%

    async def test_position_cap_limits_qty(self):
        """종목당 상한 1%(100만 원)이면 100주 — 리스크 수량 250주보다 작다"""
        _, buy = await run([cand()], max_position_pct=0.01)
        self.assertEqual(buy.calls, [("A", 100)])

    async def test_no_atr_is_skipped_without_order(self):
        """ATR 이 없으면 청산선이 없어 실전 청산 판정이 건너뛴다 — 그런 포지션을 만들지 않는다"""
        result, buy = await run([cand(atr=0.0)])
        self.assertEqual((buy.calls, result.skipped[pa.SKIP_NO_ATR]), ([], 1))

    async def test_zero_qty_is_skipped(self):
        result, buy = await run([cand(price=10_000_000.0, atr=200_000.0)], snapshot=snap(1_000_000, 1_000_000))
        self.assertEqual((buy.calls, result.skipped[pa.SKIP_ZERO_QTY]), ([], 1))


class OrderingTest(unittest.IsolatedAsyncioTestCase):
    async def test_higher_conviction_first(self):
        _, buy = await run([cand("LOW", conviction=0.4), cand("HIGH", conviction=0.9), cand("MID", conviction=0.6)])
        self.assertEqual([c for c, _ in buy.calls], ["HIGH", "MID", "LOW"])

    async def test_tie_is_broken_by_code_not_input_order(self):
        _, buy = await run([cand("B"), cand("A")])
        self.assertEqual([c for c, _ in buy.calls], ["A", "B"])


class LimitsTest(unittest.IsolatedAsyncioTestCase):
    async def test_max_positions_counts_existing_holdings(self):
        held = [PoolPosition("H1", 10, 100.0, 100.0, 2.0), PoolPosition("H2", 10, 100.0, 100.0, 2.0)]
        result, buy = await run([cand("A"), cand("B")], positions=held, max_positions=3)
        self.assertEqual(len(buy.calls), 1)                              # 보유 2 + 1 = 3 에서 멈춘다
        self.assertEqual(result.skipped["max_positions"], 1)

    async def test_open_risk_limit_includes_existing_holdings(self):
        """총 열린 리스크 0.2%(20만 원): 기존 보유 12만 원 + 새 10만 원은 초과 → 보류"""
        held = [PoolPosition("H", 300, 10_000.0, 10_000.0, 200.0)]       # (10000−9600)×300 = 12만 원
        result, buy = await run([cand()], positions=held, max_open_risk_pct=0.002)
        self.assertEqual((buy.calls, result.skipped["open_risk"]), ([], 1))

    async def test_open_risk_accumulates_across_fills_in_one_cycle(self):
        """한 사이클에서 체결될 때마다 열린 리스크가 쌓여 뒤 후보는 한도에 걸린다 (0.25% = 3건째 초과)"""
        result, buy = await run([cand(c, conviction=0.9 - i * 0.1) for i, c in enumerate("ABC")],
                                max_open_risk_pct=0.0025)
        self.assertEqual([c for c, _ in buy.calls], ["A", "B"])
        self.assertEqual(result.skipped["open_risk"], 1)

    async def test_profitable_holding_does_not_use_open_risk_room(self):
        """청산선이 본전 위(이익 포지션)면 열린 리스크 0 — 한도를 차지하지 않는다"""
        held = [PoolPosition("H", 300, 10_000.0, 14_000.0, 200.0)]
        result, buy = await run([cand()], positions=held, max_open_risk_pct=0.002)
        self.assertEqual(len(buy.calls), 1)

    async def test_cash_is_consumed_so_money_is_not_spent_twice(self):
        """현금 600만 원: 250만 원 2건까지(버퍼 포함 3번째는 부족) — 같은 돈을 두 번 쓰지 않는다"""
        result, buy = await run([cand(c, conviction=0.9 - i * 0.1) for i, c in enumerate("ABC")],
                                snapshot=snap(cash=6_000_000))
        self.assertEqual([c for c, _ in buy.calls], ["A", "B"])
        self.assertEqual(result.skipped["cash"], 1)

    async def test_cash_buffer_is_required(self):
        """주문 금액 250만 원은 현금 250만 원으로는 안 된다 — 시장가 체결이 현재가보다 높을 수 있어 1% 여유"""
        result, buy = await run([cand()], snapshot=snap(cash=2_500_000))
        self.assertEqual((buy.calls, result.skipped["cash"]), ([], 1))

    async def test_liquidity_blocks_including_unknown_adv(self):
        r1, b1 = await run([cand(adv=1_000_000.0)])                       # 250만 원 > 100만 원 × 5%
        r2, b2 = await run([cand(adv=0.0)])
        self.assertEqual((b1.calls, b2.calls), ([], []))
        self.assertEqual((r1.skipped["liquidity"], r2.skipped["liquidity"]), (1, 1))

    async def test_rejected_candidates_are_never_ordered(self):
        result, buy = await run([cand("A", atr=0.0), cand("B", adv=1.0)])
        self.assertEqual(buy.calls, [])
        self.assertEqual(result.attempted, 0)


class OutcomeTest(unittest.IsolatedAsyncioTestCase):
    async def test_partial_fill_uses_actual_qty_for_state(self):
        """100주만 체결 — 현금·리스크를 실제 체결분으로 갱신해 뒤 후보가 남은 돈으로 판단된다"""
        buy = Recorder(lambda c, qty: BuyOutcome(BUY_FILLED, 100, c.price))
        result, _ = await run([cand("A", conviction=0.9), cand("B", conviction=0.5)],
                              buy=buy, snapshot=snap(cash=3_800_000))
        # A 계획 250주(250만 원)에 100주(100만 원)만 체결 → 현금 약 270만 원 남아 B(250만 원 × 1.01)도 가능
        self.assertEqual([c for c, _ in buy.calls], ["A", "B"])

    async def test_unconfirmed_reserves_planned_amount_and_risk(self):
        """체결 미확인은 체결됐을 수 있다 — 계획 금액·위험을 반영해 뒤 후보가 같은 돈을 쓰지 않는다"""
        buy = Recorder(BuyOutcome(BUY_UNCONFIRMED))
        result, _ = await run([cand("A", conviction=0.9), cand("B", conviction=0.5)],
                              buy=buy, snapshot=snap(cash=3_000_000))
        self.assertEqual([c for c, _ in buy.calls], ["A"])
        self.assertEqual(result.skipped["cash"], 1)
        self.assertEqual(result.filled, 1)

    async def test_adopted_counts_a_position_but_not_cash(self):
        """이미 보유 중이라 편입 — 보유 수·리스크는 늘고 현금은 그대로"""
        buy = Recorder(lambda c, qty: BuyOutcome(BUY_ADOPTED, 50, 10_000.0))
        result, _ = await run([cand("A", conviction=0.9), cand("B", conviction=0.5)],
                              buy=buy, max_positions=1)
        self.assertEqual([c for c, _ in buy.calls], ["A"])
        self.assertEqual(result.skipped["max_positions"], 1)

    async def test_failed_buy_does_not_change_state(self):
        calls = iter([BuyOutcome(BUY_FAILED), None])
        buy = Recorder(lambda c, qty: next(calls) or BuyOutcome(BUY_FILLED, qty, c.price))
        result, _ = await run([cand("A", conviction=0.9), cand("B", conviction=0.5)], buy=buy,
                              max_positions=1)
        self.assertEqual([c for c, _ in buy.calls], ["A", "B"])          # 실패는 한도를 소모하지 않는다

    async def test_aborts_after_consecutive_failures(self):
        """외부 장애로 연속 실패하면 사이클을 중단한다 — 계속 두드리지 않는다"""
        buy = Recorder(BuyOutcome(BUY_FAILED))
        result, _ = await run([cand(c, conviction=0.9 - i * 0.05) for i, c in enumerate("ABCDEF")], buy=buy)
        self.assertEqual(len(buy.calls), pa.MAX_CONSECUTIVE_FAILURES)
        self.assertEqual(result.skipped[pa.SKIP_ABORTED], 3)

    async def test_success_resets_failure_streak(self):
        seq = iter([BUY_FAILED, BUY_FAILED, BUY_FILLED, BUY_FAILED, BUY_FAILED, BUY_FILLED])
        buy = Recorder(lambda c, qty: BuyOutcome(next(seq), qty, c.price))
        _, rec = await run([cand(c, conviction=0.9 - i * 0.05, swing_id=i) for i, c in enumerate("ABCDEF")],
                           buy=buy, s=setting(max_positions=None, max_open_risk_pct=0.2, risk_pct=0.0001))
        self.assertEqual(len(buy.calls), 6)

    async def test_empty_candidates(self):
        result, buy = await run([])
        self.assertEqual((buy.calls, result.attempted, result.filled), ([], 0, 0))


class DefaultConfigTest(unittest.IsolatedAsyncioTestCase):
    """현재 기본값(집중형: 0.70%·10종목·9.5%) — 10종목에 균등하게 총자산의 약 95%를 담는다"""

    def default_setting(self):
        return PoolSetting.create("acc", "KR", use_yn="Y")

    def candidates(self, n, atr):
        return [cand(code=f"S{i:02d}", atr=atr, conviction=1 - i * 0.01, swing_id=i) for i in range(n)]

    async def run_defaults(self, n, atr):
        rec = Recorder()
        result = await run_pool_entries(
            self.default_setting(), self.candidates(n, atr), AccountSnapshot("KR", EQUITY, EQUITY), [], rec)
        return rec, result

    async def test_defaults_are_the_agreed_concentrated_config(self):
        s = self.default_setting()
        self.assertEqual((s.RISK_PCT, s.MAX_POSITIONS, s.MAX_POSITION_PCT, s.MAX_OPEN_RISK_PCT),
                         (Decimal("0.0070"), 10, Decimal("0.095"), Decimal("0.08")))

    async def test_every_stock_is_capped_at_equal_weight_whatever_the_stop_width(self):
        """손절폭이 7% 이하면 0.70% 리스크는 9.5% 를 넘게 계산되어 모든 종목이 같은 비중으로 잘린다"""
        for atr in (100.0, 250.0, 600.0, 1500.0):      # 손절폭 2%·5%·7%(ATR×2=12% → 7% 상한)·7%
            rec, _ = await self.run_defaults(1, atr)
            self.assertEqual(rec.calls, [("S00", 950)], f"atr={atr}")      # 1억 × 9.5% ÷ 10,000원

    async def test_ten_fit_and_the_eleventh_is_not_bought(self):
        """10종목이 차면 현금이 약 5%만 남아 11번째는 종목 수 한도보다 먼저 현금에서 걸린다 — 어느 쪽이든 사지 않는다"""
        rec, result = await self.run_defaults(11, 250.0)
        self.assertEqual(len(rec.calls), 10)
        self.assertEqual(sum(result.skipped.values()), 1)
        self.assertEqual(result.skipped[risk_sizing.REJECT_CASH] + result.skipped[risk_sizing.REJECT_MAX_POSITIONS], 1)
        self.assertEqual([c for c, _ in rec.calls], [f"S{i:02d}" for i in range(10)], "신호 강도 순서로 슬롯을 채운다")

    async def test_count_limit_alone_blocks_the_eleventh_when_cash_is_plentiful(self):
        """종목 수 한도 자체가 동작하는지 — 현금이 넉넉하면(총자산 대비 작은 포지션) 한도에서 걸린다"""
        rec = Recorder()
        result = await run_pool_entries(
            PoolSetting.create("acc", "KR", use_yn="Y", risk_pct="0.0010"),   # 한 종목 약 1.4% → 현금은 남아도는 설정
            self.candidates(11, 250.0), AccountSnapshot("KR", EQUITY, EQUITY), [], rec)
        self.assertEqual(len(rec.calls), 10)
        self.assertEqual(result.skipped[risk_sizing.REJECT_MAX_POSITIONS], 1)

    async def test_the_tenth_stock_still_has_cash_so_cash_is_not_the_binding_limit(self):
        """10종목 × 9.5% = 95% — 수수료·호가 여유(1%)를 얹어도 10번째가 현금 부족으로 막히지 않는다"""
        rec, result = await self.run_defaults(10, 250.0)
        self.assertEqual(len(rec.calls), 10)
        self.assertEqual(result.skipped[risk_sizing.REJECT_CASH], 0)

    async def test_any_allowed_count_fills_evenly_to_about_95_percent(self):
        """종목 수를 7~30 어디로 정해도: N종목이 같은 비중으로 모두 체결되고 N+1번째는 사지 않는다"""
        for n in (7, 10, 15, 20, 30):
            setting = PoolSetting.create("acc", "KR", use_yn="Y", max_positions=n)
            rec = Recorder()
            result = await run_pool_entries(
                setting, self.candidates(n + 1, 250.0), AccountSnapshot("KR", EQUITY, EQUITY), [], rec)
            self.assertEqual(len(rec.calls), n, f"N={n}")
            self.assertEqual(len({q for _, q in rec.calls}), 1, f"N={n}: 종목마다 수량이 같아야(균등) 한다")
            invested = sum(q for _, q in rec.calls) * 10_000
            self.assertLessEqual(invested, EQUITY * 0.95, f"N={n}")
            self.assertGreater(invested, EQUITY * 0.93, f"N={n}: 약 95% 가 투입돼야 한다")
            self.assertEqual(sum(result.skipped.values()), 1, f"N={n}: 넘치는 1개는 건너뜀")

    async def test_total_open_risk_stays_inside_the_limit(self):
        """10종목이 모두 최대 손절폭(7%)이어도 9.5% × 7% × 10 = 6.65% 로 미청산 리스크 한도 8% 안"""
        rec, result = await self.run_defaults(10, 1500.0)
        self.assertEqual(len(rec.calls), 10)
        self.assertEqual(result.skipped[risk_sizing.REJECT_OPEN_RISK], 0)


if __name__ == "__main__":
    unittest.main()
