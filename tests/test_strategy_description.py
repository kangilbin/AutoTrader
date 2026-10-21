"""
실전 전략 설명(describe) 테스트

실행:
  uv run pytest tests/test_strategy_description.py -v

AI 종목 분석은 TradingStrategyFactory 에 등록된 전략의 describe() 를 그대로 Gemini에
넘기고, 그 중 하나를 고르게 한다. 그래서
- 등록된 전략은 모두 설명을 가져야 하고 (전략 클래스는 인스턴스화되지 않아 abstractmethod 로 강제되지 않는다)
- 설명 속 수치는 판정 로직과 같은 상수여야 하며 (손으로 쓴 설명은 실제 동작과 어긋났었다)
- Gemini는 등록된 키 밖의 전략을 고를 수 없어야 한다.
"""
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.domain.stock_review.prompt import build_analysis_instruction, build_strategy_instruction
from app.domain.stock_review.schemas import strategy_pick_schema
from app.domain.swing.trading.strategies.single_ema_strategy import SingleEMAStrategy
from app.domain.swing.trading.trading_strategy_factory import TradingStrategyFactory

REQUIRED_KEYS = {"이름", "요약", "매수조건", "매수차단", "매수수량", "청산", "매도후", "잘맞는종목", "안맞는종목"}


class DescribeAllTest(unittest.TestCase):

    def test_every_registered_strategy_is_described(self):
        descriptions = TradingStrategyFactory.describe_all()
        self.assertEqual(set(descriptions), set(TradingStrategyFactory._strategies))
        for code, desc in descriptions.items():
            with self.subTest(code=code):
                self.assertEqual(REQUIRED_KEYS - set(desc), set(), f"{code} 설명에 빠진 항목")


class SingleEMADescriptionTest(unittest.TestCase):

    def test_numbers_follow_constants(self):
        """상수를 바꾸면 설명도 바뀐다 — 설명이 실제 판정과 어긋나지 않게"""
        with patch.object(SingleEMAStrategy, "MAX_STOP_LOSS_PCT", 9.0), \
                patch.object(SingleEMAStrategy, "BREAKOUT_ENTRY_ADX_MIN", 18):
            desc = SingleEMAStrategy.describe()
        self.assertIn("진입가 −9%", desc["청산"][0])
        self.assertIn("ADX 18 초과", desc["매수조건"][1])

    def test_partial_take_profit_only_when_enabled(self):
        """PARTIAL_TAKE_PROFIT_PCT <= 0 이면 부분익절이 꺼지므로 설명에서도 빠져야 한다"""
        self.assertTrue(any("부분익절" in line for line in SingleEMAStrategy.describe()["청산"]))
        with patch.object(SingleEMAStrategy, "PARTIAL_TAKE_PROFIT_PCT", 0):
            self.assertFalse(any("부분익절" in line for line in SingleEMAStrategy.describe()["청산"]))

    def test_surge_filter_is_today_not_yesterday(self):
        """급등 필터는 당일 등락률 기준 (check_entry_signal 의 prdy_ctrt)"""
        self.assertIn("당일 등락률 ±5%", SingleEMAStrategy.describe()["매수차단"][0])

    def test_sizing_range(self):
        """신호 강도 사이징 = MAX_ENTRY_PCT × [MIN_CONVICTION, 1]"""
        self.assertIn("32~80%", SingleEMAStrategy.describe()["매수수량"])


class StrategyPickTest(unittest.TestCase):

    def test_only_registered_swing_types(self):
        schema = strategy_pick_schema(["S"])
        self.assertEqual(schema.model_validate({"recommended_swing_type": "S"}).recommended_swing_type, "S")
        with self.assertRaises(ValidationError):
            schema.model_validate({"recommended_swing_type": "A"})

    def test_strategy_is_mandatory(self):
        """2단계에선 '맞는 전략 없음'을 허용하지 않는다 — 반드시 하나를 고른다"""
        with self.assertRaises(ValidationError):
            strategy_pick_schema(["S"]).model_validate({"recommended_swing_type": None})

    def test_strategy_instruction_lists_strategies(self):
        text = build_strategy_instruction(TradingStrategyFactory.describe_all())
        self.assertIn('"S"', text)
        self.assertIn(SingleEMAStrategy.name, text)

    def test_analysis_instruction_has_no_strategy(self):
        """투자 판단 지시문에 전략 정보가 섞이면 판단이 전략 진입 조건 관점으로 쏠린다"""
        for market in ("KR", "US"):
            text = build_analysis_instruction(market)
            for word in ("SWING_TYPE", SingleEMAStrategy.name, "20EMA", "OBV", "ATR", "청산선"):
                self.assertNotIn(word, text, f"{market}: {word}")


if __name__ == "__main__":
    unittest.main()
