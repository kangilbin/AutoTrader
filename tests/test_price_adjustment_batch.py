"""
수정주가 재적재 배치 - 유닛 테스트

실행:
  PYTHONPATH=. python -m unittest tests.test_price_adjustment_batch -v
  또는 (pytest 설치 시):
  pytest tests/test_price_adjustment_batch.py -v
"""
import unittest

from app.domain.stock.price_adjustment_batch import _normalize_date


class TestNormalizeDate(unittest.TestCase):
    """_normalize_date: KIS 응답의 다양한 날짜 포맷을 YYYYMMDD 8자로 통일"""

    def test_normalize(self):
        cases = [
            ("20260617", "20260617"),
            ("2026/06/17", "20260617"),
            ("2026-06-17", "20260617"),
            ("2026.06.17", "20260617"),
            ("", ""),
            (None, ""),
            ("2026/06/17 ~ 2026/06/30", "20260617"),
        ]
        for input_str, expected in cases:
            with self.subTest(input=input_str):
                self.assertEqual(_normalize_date(input_str), expected)


if __name__ == "__main__":
    unittest.main()