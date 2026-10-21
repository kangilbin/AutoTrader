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


NY = ZoneInfo("America/New_York")


class UsCacheTtlTest(unittest.TestCase):
    """미국은 뉴욕 현지 시각으로 장중·다음 개장(09:30)을 판단한다"""

    def test_us_intraday_is_short(self):
        now = datetime(2026, 10, 21, 11, 0, tzinfo=NY).astimezone(KST)   # 뉴욕 수요일 11:00
        self.assertEqual(_cache_ttl(now, "NAS"), _INTRADAY_TTL)

    def test_us_after_close_until_next_open(self):
        now = datetime(2026, 10, 21, 17, 0, tzinfo=NY).astimezone(KST)   # 뉴욕 수요일 장 마감 후
        self.assertEqual(_cache_ttl(now, "NYS"), _until(now, datetime(2026, 10, 22, 9, 30, tzinfo=NY)))

    def test_us_friday_after_close_until_monday(self):
        now = datetime(2026, 10, 23, 18, 0, tzinfo=NY).astimezone(KST)
        self.assertEqual(_cache_ttl(now, "AMS"), _until(now, datetime(2026, 10, 26, 9, 30, tzinfo=NY)))

    def test_korean_daytime_is_us_closed(self):
        """한국 낮(뉴욕 새벽)은 국내 장중이어도 미국 기준으론 장 밖이다"""
        now = _at(2026, 10, 21, 11)
        self.assertEqual(_cache_ttl(now, "J"), _INTRADAY_TTL)
        self.assertGreater(_cache_ttl(now, "NAS"), _INTRADAY_TTL)


if __name__ == "__main__":
    unittest.main()
