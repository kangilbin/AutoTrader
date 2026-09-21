"""
백테스트 데이터 충분성 검증 회귀 테스트

실행:
  uv run pytest tests/test_backtest_data_sufficiency.py -v

문제: 데이터 적재는 스윙 활성화 시점에만 일어나므로, 활성화 전 종목은
STOCK_DAY_HISTORY 가 비어 있거나 부족하다. 그런데 백테스트는 0건만 걸러내고
1건이라도 있으면 3년 백테스트를 수행한 것처럼 응답했다.

특히 일목균형표는 tech_analysis 의 길이 가드가 신호를 전부 False 로 돌려주므로,
부족한 데이터에서 "거래 0건 / 수익률 0%" 라는 그럴듯한 결과가 나왔다.
사용자는 이를 '이 전략은 거래가 없었다'로 읽지만 실제로는 계산이 불가능했던 것이다.
"""
import unittest
from datetime import datetime

from dateutil.relativedelta import relativedelta

from app.domain.swing.backtest import backtest_service as svc
from app.domain.swing.backtest.strategy_factory import StrategyFactory
from app.domain.swing.tech_analysis import ICHIMOKU_MIN_BARS
from app.exceptions.domain import ValidationError


def _bars(n: int, end: datetime = None) -> list:
    """거래일 n개 분량의 행 (최신이 end, 하루씩 거슬러 올라감)"""
    end = end or datetime.now()
    return [
        {"STCK_BSOP_DATE": (end - relativedelta(days=i)).strftime("%Y%m%d"),
         "STCK_CLPR": 1000 + i}
        for i in range(n)
    ]


class MinBarsTest(unittest.TestCase):
    """최소 봉 수는 지표 기간을 아는 전략이 답한다"""

    def test_ichimoku_matches_indicator_guard(self):
        """B 전략의 최소치가 tech_analysis 의 길이 가드와 같은 값이어야 한다

        두 곳에 따로 적으면 한쪽만 바뀌어 '가드는 막는데 사전검증은 통과'가 된다.
        """
        self.assertEqual(
            StrategyFactory.get_strategy("B").min_bars({}), ICHIMOKU_MIN_BARS
        )
        self.assertEqual(ICHIMOKU_MIN_BARS, 79)

    def test_single_ema_covers_long_ema_warmup(self):
        """S 전략은 장기 EMA(120) 워밍업을 덮어야 한다"""
        strategy = StrategyFactory.get_strategy("S")
        self.assertEqual(strategy.min_bars({}), strategy.EMA_LONG_PERIOD + 1)

    def test_ema_strategy_follows_requested_long_term(self):
        """A 전략은 요청값(long_term)에 따라 최소치가 달라진다 (하드코딩 금지)"""
        strategy = StrategyFactory.get_strategy("A")
        self.assertEqual(strategy.min_bars({"long_term": 60}), 61)
        self.assertEqual(strategy.min_bars({"long_term": 200}), 201)


class SufficiencyGuardTest(unittest.TestCase):

    def setUp(self):
        self.eval_start = datetime.now() - relativedelta(years=2)

    def _range(self, n):
        return svc._describe_range(_bars(n), self.eval_start)

    def test_ichimoku_below_guard_is_rejected(self):
        """79봉 미만 일목균형표는 '거래 0건'이 아니라 오류로 막힌다"""
        with self.assertRaises(ValidationError) as cm:
            svc._validate_sufficiency("B", ICHIMOKU_MIN_BARS, self._range(78))

        msg = str(cm.exception)
        self.assertIn("79", msg, "필요 봉 수가 메시지에 없다")
        self.assertIn("78", msg, "보유 봉 수가 메시지에 없다")

    def test_single_ema_below_warmup_is_rejected(self):
        """121봉 미만 S 전략은 오류로 막힌다"""
        with self.assertRaises(ValidationError):
            svc._validate_sufficiency("S", 121, self._range(120))

    def test_eval_window_too_short_is_rejected(self):
        """워밍업은 채웠지만 평가할 구간이 없으면 막는다

        전체 봉 수만 보면 통과하지만, 평가구간이 비면 거래가 일어날 수 없어
        '수익률 0%'가 나온다. 이것도 조용한 실패다.
        """
        old_bars = _bars(300, end=self.eval_start - relativedelta(days=10))
        data_range = svc._describe_range(old_bars, self.eval_start)
        self.assertEqual(data_range["eval"], 0, "테스트 전제: 평가구간이 비어야 한다")

        with self.assertRaises(ValidationError) as cm:
            svc._validate_sufficiency("S", 121, data_range)
        self.assertIn("평가 구간", str(cm.exception))

    def test_sufficient_data_passes(self):
        """충분하면 통과한다 (회귀 방지)"""
        svc._validate_sufficiency("S", 121, self._range(700))   # 약 2년 이상

    def test_boundary_is_inclusive(self):
        """정확히 필요 봉 수만큼이면 통과한다 (off-by-one 고정)"""
        svc._validate_sufficiency("B", ICHIMOKU_MIN_BARS, self._range(ICHIMOKU_MIN_BARS))


class DescribeRangeTest(unittest.TestCase):

    def test_counts_and_bounds(self):
        """구간 요약은 거래일(행 개수) 기준이다 — 달력 일수로 환산하지 않는다"""
        eval_start = datetime.now() - relativedelta(years=2)
        rows = _bars(100)

        r = svc._describe_range(rows, eval_start)

        self.assertEqual(r["total"], 100)
        self.assertEqual(r["eval"], 100, "최근 100일은 전부 평가구간 안이다")
        self.assertLess(r["start"], r["end"], "start/end 가 뒤집혔다")

    def test_eval_count_excludes_warmup(self):
        """평가구간 밖(워밍업)의 봉은 eval 에서 빠진다"""
        eval_start = datetime.now() - relativedelta(years=2)
        rows = _bars(30) + _bars(30, end=eval_start - relativedelta(days=1))

        r = svc._describe_range(rows, eval_start)

        self.assertEqual(r["total"], 60)
        self.assertEqual(r["eval"], 30)


if __name__ == "__main__":
    unittest.main()
