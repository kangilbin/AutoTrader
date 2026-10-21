"""
종목 리뷰 판단 → 전략 선택 흐름 테스트 (Gemini 호출 없이)

실행:
  uv run pytest tests/test_stock_review_judge.py -v

투자 판단과 전략 선택을 나눴다 — 판단 프롬프트에 전략 설명을 함께 주면 모델이 투자 판단을
전략 진입 조건(20EMA·OBV·손절선) 관점으로만 설명했다.
- 판단이 '적합'일 때만 전략을 고른다
- 등록 전략이 하나면 Gemini를 부르지 않는다 (무료 일일 한도 절약)
- 전략 선택이 실패해도 판단은 돌려준다
"""
import asyncio
import unittest
from unittest.mock import patch

from app.domain.stock_review.schemas import GeminiReview
from app.domain.stock_review.service import CollectedData, StockReviewService, _now
from app.exceptions import ExternalServiceError

STRATEGIES = {"S": {"이름": "단일 이평선"}, "X": {"이름": "다른 전략"}}


def _review(verdict: str) -> GeminiReview:
    return GeminiReview(verdict=verdict, summary="s", news_market="n", valuation="v", financials="f",
                        trend_flow="t", positives=[], risks=[], key_news=[])


def _collected() -> CollectedData:
    return CollectedData(mrkt="J", st_code="005930", st_nm="삼성전자", now=_now(), as_of="x",
                         data={"일봉_지표": {}}, last_date="20261021", bar_incomplete=False)


class _Pick:
    def __init__(self, swing_type):
        self.recommended_swing_type = swing_type


class JudgeTest(unittest.TestCase):

    def _judge(self, verdict: str, strategies: dict, pick=None):
        calls = []

        async def fake_generate(prompt, schema, *, system_instruction=None):
            calls.append(schema.__name__)
            if schema is GeminiReview:
                return _review(verdict), "m"
            if isinstance(pick, Exception):
                raise pick
            return _Pick(pick), "m"

        with patch("app.domain.stock_review.service.gemini_api.generate_structured", fake_generate), \
                patch("app.domain.stock_review.service.TradingStrategyFactory.describe_all", return_value=strategies):
            result = asyncio.run(StockReviewService.judge(_collected()))
        return result, calls

    def test_hold_has_no_strategy(self):
        result, calls = self._judge("보류", STRATEGIES)
        self.assertIsNone(result.RECOMMENDED_SWING_TYPE)
        self.assertIsNone(result.RECOMMENDED_STRATEGY_NAME)
        self.assertEqual(calls, ["GeminiReview"])

    def test_suitable_single_strategy_without_extra_call(self):
        result, calls = self._judge("적합", {"S": STRATEGIES["S"]})
        self.assertEqual(result.RECOMMENDED_SWING_TYPE, "S")
        self.assertEqual(result.RECOMMENDED_STRATEGY_NAME, "단일 이평선")
        self.assertEqual(calls, ["GeminiReview"])

    def test_suitable_multiple_strategies_asks_second_step(self):
        result, calls = self._judge("적합", STRATEGIES, pick="X")
        self.assertEqual(result.RECOMMENDED_SWING_TYPE, "X")
        self.assertEqual(result.RECOMMENDED_STRATEGY_NAME, "다른 전략")
        self.assertEqual(calls, ["GeminiReview", "StrategyPick"])

    def test_strategy_pick_failure_keeps_verdict(self):
        result, _ = self._judge("적합", STRATEGIES, pick=ExternalServiceError("Gemini", "혼잡"))
        self.assertEqual(result.VERDICT, "적합")
        self.assertIsNone(result.RECOMMENDED_SWING_TYPE)


if __name__ == "__main__":
    unittest.main()
