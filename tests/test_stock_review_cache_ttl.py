"""
종목 리뷰 결과 캐시 유지 시간 테스트

실행:
  uv run pytest tests/test_stock_review_cache_ttl.py -v

장 밖에서는 시세·수급이 바뀌지 않으므로 다음 평일 개장까지 결과를 재사용한다.
주말을 고려하지 않으면 금요일 오후 결과가 토요일 09:00에 만료돼, 데이터가 같은데도
주말마다 KIS 15회 + Gemini(무료 쿼터) 호출을 다시 한다.
"""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.stock_review.service import _cache_ttl, _INTRADAY_TTL

KST = ZoneInfo("Asia/Seoul")


def _at(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=KST)


def _until(now: datetime, target: datetime) -> int:
    return int((target - now).total_seconds())


class CacheTtlTest(unittest.TestCase):
    # 2026-10-23 금요일, 2026-10-26 월요일

    def test_intraday_is_short(self):
        self.assertEqual(_cache_ttl(_at(2026, 10, 21, 10)), _INTRADAY_TTL)

    def test_weekday_before_open_until_same_day_open(self):
        now = _at(2026, 10, 21, 8)
        self.assertEqual(_cache_ttl(now), _until(now, _at(2026, 10, 21, 9)))

    def test_weekday_after_close_until_next_day_open(self):
        now = _at(2026, 10, 21, 16)
        self.assertEqual(_cache_ttl(now), _until(now, _at(2026, 10, 22, 9)))

    def test_friday_after_close_until_monday_open(self):
        now = _at(2026, 10, 23, 16)
        self.assertEqual(_cache_ttl(now), _until(now, _at(2026, 10, 26, 9)))

    def test_saturday_until_monday_open(self):
        now = _at(2026, 10, 24, 12)
        self.assertEqual(_cache_ttl(now), _until(now, _at(2026, 10, 26, 9)))

    def test_sunday_morning_until_monday_open(self):
        """일요일 09:00 이전도 '오늘 개장'이 아니라 월요일 개장까지"""
        now = _at(2026, 10, 25, 8)
        self.assertEqual(_cache_ttl(now), _until(now, _at(2026, 10, 26, 9)))


if __name__ == "__main__":
    unittest.main()
