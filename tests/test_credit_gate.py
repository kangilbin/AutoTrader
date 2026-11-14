"""
신용 스프레드 진입 게이트(Q3) — 하이일드 스프레드가 5개 관측치 전보다 낮고 2.9% 이상일 때만 신규 매수를 허용한다

실행: uv run pytest tests/test_credit_gate.py -v

구성
  1) FRED CSV 파싱
  2) 판정(decide): 하락 중·수준 조건, 경계값, 데이터 부족·낡음(fail-open)
  3) Redis 저장·조회와 check (비활성·Redis 오류 시 허용)
근거 사례: SNOW 2026-10-12(스프레드 5일 +0.01%p → 차단), 2026-10-28(5일 −0.15%p → 허용)
"""
import logging
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from app.domain.swing.trading import auto_swing_batch as batch
from app.domain.swing.trading import credit_gate as cg
from app.external import fred_api
from tests.test_swing_trade_scenarios import FakeRedis, Market, SwingScenarioBase

TODAY = date(2026, 11, 12)


def series(*values, end=date(2026, 11, 11)):
    """값 목록(오래된 것부터)을 end 로 끝나는 연속 날짜의 관측치로 만든다"""
    n = len(values)
    return [(end - timedelta(days=n - 1 - i), v) for i, v in enumerate(values)]


class FredParseTest(unittest.TestCase):
    def test_parse_skips_header_and_holidays(self):
        text = "observation_date,BAMLH0A0HYM2\n2026-11-02,3.43\n2026-11-03,3.40\n2026-11-04,.\n2026-11-05,3.48\n"
        self.assertEqual(fred_api.parse_csv(text), [(date(2026, 11, 2), 3.43), (date(2026, 11, 3), 3.40), (date(2026, 11, 5), 3.48)])

    def test_parse_sorts_ascending_and_ignores_broken_rows(self):
        text = "observation_date,X\n2026-11-05,3.48\nnot-a-date,3.1\n2026-11-03,abc\n2026-11-02,3.43\n"
        self.assertEqual(fred_api.parse_csv(text), [(date(2026, 11, 2), 3.43), (date(2026, 11, 5), 3.48)])

    def test_parse_empty_body_returns_empty(self):
        self.assertEqual(fred_api.parse_csv(""), [])
        self.assertEqual(fred_api.parse_csv("observation_date,X\n"), [])


class FredApiKeyTest(unittest.IsolatedAsyncioTestCase):
    """FRED_API_KEY 가 있으면 공식 JSON API, 없으면 CSV — 어느 경우에도 키가 로그·오류에 남지 않는다"""

    SECRET = "abcd1234secretkeyabcd1234secret12"

    def test_parse_json_skips_holidays_and_sorts(self):
        body = {"observations": [{"date": "2026-11-05", "value": "3.48"}, {"date": "2026-11-04", "value": "."},
                                 {"date": "2026-11-03", "value": "3.40"}, {"date": "bad", "value": "1"}, {"date": "2026-11-02"}]}
        self.assertEqual(fred_api.parse_json(body), [(date(2026, 11, 3), 3.40), (date(2026, 11, 5), 3.48)])
        self.assertEqual(fred_api.parse_json({}), [])
        self.assertEqual(fred_api.parse_json({"observations": None}), [])

    async def _call(self, key, response_factory):
        """httpx.AsyncClient.get 을 가짜로 바꿔 요청 주소·파라미터를 기록한다"""
        calls = []

        class FakeClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, params=None):
                calls.append((url, dict(params or {})))
                return response_factory(url)

        settings = type("S", (), {"FRED_API_KEY": key})()
        with patch.object(fred_api, "get_settings", return_value=settings), patch.object(fred_api.httpx, "AsyncClient", FakeClient):
            rows = await fred_api.get_series("BAMLH0A0HYM2")
        return rows, calls

    @staticmethod
    def _ok(json_body=None, text=""):
        import httpx
        def make(url):
            req = httpx.Request("GET", url)
            if json_body is not None:
                return httpx.Response(200, json=json_body, request=req)
            return httpx.Response(200, text=text, request=req)
        return make

    async def test_with_key_uses_official_json_api(self):
        body = {"observations": [{"date": "2026-11-11", "value": "3.30"}, {"date": "2026-11-12", "value": "3.20"}]}
        rows, calls = await self._call(self.SECRET, self._ok(json_body=body))
        self.assertEqual(rows, [(date(2026, 11, 11), 3.30), (date(2026, 11, 12), 3.20)])
        url, params = calls[0]
        self.assertIn("api.stlouisfed.org", url)
        self.assertEqual((params["series_id"], params["file_type"], params["api_key"]), ("BAMLH0A0HYM2", "json", self.SECRET))

    async def test_without_key_uses_csv(self):
        csv_text = "observation_date,BAMLH0A0HYM2\n2026-11-11,3.30\n2026-11-12,3.20\n"
        for key in (None, "", "   "):
            rows, calls = await self._call(key, self._ok(text=csv_text))
            self.assertEqual(rows, [(date(2026, 11, 11), 3.30), (date(2026, 11, 12), 3.20)])
            self.assertIn("fredgraph.csv", calls[0][0])
            self.assertNotIn("api_key", calls[0][1])

    async def test_http_error_message_never_contains_key(self):
        import httpx
        def failing(url):
            return httpx.Response(403, request=httpx.Request("GET", f"{url}?api_key={self.SECRET}"))
        with self.assertRaises(fred_api.ExternalServiceError) as cm:
            await self._call(self.SECRET, failing)
        self.assertNotIn(self.SECRET, str(cm.exception))
        self.assertIn("403", str(cm.exception))

    async def test_empty_or_invalid_response_is_error(self):
        for factory in (self._ok(json_body={"observations": []}), self._ok(json_body={"error_message": "x"})):
            with self.assertRaises(fred_api.ExternalServiceError):
                await self._call(self.SECRET, factory)

    def test_httpx_log_filter_redacts_api_key(self):
        """httpx 가 URL 째로 INFO 로그에 남겨도 api_key 값은 가려진다"""
        flt = fred_api._RedactApiKeyFilter()
        url = f"https://api.stlouisfed.org/fred/series/observations?series_id=X&api_key={self.SECRET}&file_type=json"
        record = logging.LogRecord("httpx", logging.INFO, "", 0, 'HTTP Request: %s %s "%s %d %s"', ("GET", url, "HTTP/1.1", 200, "OK"), None)
        flt.filter(record)
        self.assertNotIn(self.SECRET, record.getMessage())
        self.assertIn("api_key=***", record.getMessage())
        self.assertIn("series_id=X", record.getMessage(), "다른 파라미터는 남긴다")

    def test_log_filter_is_installed_once_on_httpx_logger(self):
        fred_api._install_log_filter(); fred_api._install_log_filter()
        installed = [f for f in logging.getLogger("httpx").filters if isinstance(f, fred_api._RedactApiKeyFilter)]
        self.assertEqual(len(installed), 1)


class DecideTest(unittest.TestCase):
    def test_falling_and_above_min_level_is_allowed(self):
        """5개 전 3.40 → 현재 3.30 (하락 중, 2.9% 이상) → 허용 — 2026-11-11 실제 값"""
        d = cg.decide(series(3.40, 3.32, 3.48, 3.42, 3.34, 3.30), TODAY)
        self.assertTrue(d.allowed)
        self.assertEqual((d.prior, d.level), (3.40, 3.30))

    def test_rising_is_blocked(self):
        """5개 전 3.11 → 현재 3.12 (SNOW 2026-10-12: +0.01%p) → 차단"""
        d = cg.decide(series(3.11, 3.10, 3.15, 3.08, 3.20, 3.12), TODAY)
        self.assertFalse(d.allowed)
        self.assertIn("상승", d.reason)

    def test_unchanged_is_blocked(self):
        """같은 값은 '낮다'가 아니다 — 정체도 차단"""
        self.assertFalse(cg.decide(series(3.30, 3.1, 3.2, 3.4, 3.3, 3.30), TODAY).allowed)

    def test_below_min_level_is_blocked_even_if_falling(self):
        """하락 중이어도 2.9% 미만(평온 구간)이면 차단"""
        d = cg.decide(series(2.95, 2.90, 2.85, 2.80, 2.78, 2.75), TODAY)
        self.assertFalse(d.allowed)
        self.assertIn("평온", d.reason)

    def test_min_level_boundary_is_inclusive(self):
        """정확히 2.9% 는 허용 (>=)"""
        self.assertTrue(cg.decide(series(3.0, 2.99, 2.95, 2.93, 2.91, 2.90), TODAY).allowed)
        self.assertFalse(cg.decide(series(3.0, 2.99, 2.95, 2.93, 2.91, 2.89), TODAY).allowed)

    def test_compares_with_observation_exactly_lookback_back(self):
        """비교 대상은 정확히 5개 전 관측치 — 4개 전이나 6개 전이 아니다 (백테스트의 5거래일 변화)"""
        # 값: [..., 6개 전=9.0(무관), 5개 전=3.31, 4개 전=1.0, ..., 현재=3.30]
        obs = series(9.0, 3.31, 1.0, 1.0, 1.0, 1.0, 3.30)
        d = cg.decide(obs, TODAY)
        self.assertEqual(d.prior, 3.31)
        self.assertTrue(d.allowed, "3.31 → 3.30 하락")

    def test_too_few_observations_is_fail_open(self):
        d = cg.decide(series(3.0, 3.1, 3.2, 3.3, 3.4), TODAY)       # 5개 < 6개
        self.assertTrue(d.allowed)
        self.assertIn("부족", d.reason)
        self.assertTrue(cg.decide([], TODAY).allowed)

    def test_stale_data_is_fail_open(self):
        """가장 최근 관측일이 STALE_DAYS 를 넘으면 판단하지 않고 허용 — 갱신 실패로 매매가 막히면 안 된다"""
        old = series(3.0, 3.1, 3.2, 3.3, 3.4, 3.5, end=TODAY - timedelta(days=cg.STALE_DAYS + 1))
        d = cg.decide(old, TODAY)
        self.assertTrue(d.allowed)
        self.assertIn("판단 불가", d.reason)
        fresh = series(3.0, 3.1, 3.2, 3.3, 3.4, 3.5, end=TODAY - timedelta(days=cg.STALE_DAYS))
        self.assertFalse(cg.decide(fresh, TODAY).allowed, "경계(7일)는 낡지 않은 것으로 보고 판정한다 — 상승이라 차단")


class RedisStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_store_and_load_roundtrip_keeps_last_history(self):
        redis = FakeRedis()
        obs = series(*[3.0 + i * 0.01 for i in range(40)])
        await cg.store_observations(redis, obs)
        loaded = await cg.load_observations(redis)
        self.assertEqual(len(loaded), cg.HISTORY_KEEP)
        self.assertEqual(loaded[-1], obs[-1])
        self.assertEqual(loaded[0], obs[-cg.HISTORY_KEEP])

    async def test_load_missing_or_broken_returns_empty(self):
        redis = FakeRedis()
        self.assertEqual(await cg.load_observations(redis), [])
        redis.store[cg.REDIS_KEY] = "{깨진 값"
        self.assertEqual(await cg.load_observations(redis), [])

    async def test_check_allows_when_no_data(self):
        """Redis 에 값이 없으면(첫 기동 직후 등) 허용"""
        d = await cg.check(FakeRedis(), TODAY)
        self.assertTrue(d.allowed)

    async def test_check_blocks_per_stored_data(self):
        redis = FakeRedis()
        await cg.store_observations(redis, series(3.11, 3.10, 3.15, 3.08, 3.20, 3.12))
        self.assertFalse((await cg.check(redis, TODAY)).allowed)
        await cg.store_observations(redis, series(3.40, 3.32, 3.48, 3.42, 3.34, 3.30))
        self.assertTrue((await cg.check(redis, TODAY)).allowed)

    async def test_check_allows_when_redis_fails(self):
        class BrokenRedis:
            async def get(self, key):
                raise ConnectionError("redis down")
        d = await cg.check(BrokenRedis(), TODAY)
        self.assertTrue(d.allowed)
        self.assertIn("Redis", d.reason)

    async def test_check_allows_when_disabled(self):
        redis = FakeRedis()
        await cg.store_observations(redis, series(3.11, 3.10, 3.15, 3.08, 3.20, 3.12))   # 켜져 있으면 차단될 값
        with patch.object(cg, "enabled", return_value=False):
            d = await cg.check(redis, TODAY)
        self.assertTrue(d.allowed)
        self.assertIn("비활성", d.reason)

    async def test_refresh_stores_fetched_series(self):
        redis = FakeRedis()
        fetched = series(3.40, 3.32, 3.48, 3.42, 3.34, 3.30)

        async def fake_get_series(series_id, lookback_days=60):
            self.assertEqual(series_id, cg.SERIES_ID)
            return fetched

        with patch.object(cg.fred_api, "get_series", fake_get_series):
            count = await cg.refresh(redis)
        self.assertEqual(count, 6)
        self.assertEqual(await cg.load_observations(redis), fetched)

    async def test_refresh_failure_keeps_previous_values(self):
        """FRED 가 실패하면 예외를 올리고, 저장된 기존 값은 건드리지 않는다"""
        redis = FakeRedis()
        previous = series(3.40, 3.32, 3.48, 3.42, 3.34, 3.30)
        await cg.store_observations(redis, previous)

        async def failing(series_id, lookback_days=60):
            raise fred_api.ExternalServiceError("FRED", "timeout")

        with patch.object(cg.fred_api, "get_series", failing):
            with self.assertRaises(fred_api.ExternalServiceError):
                await cg.refresh(redis)
        self.assertEqual(await cg.load_observations(redis), previous)


class BatchGateScenarioTest(SwingScenarioBase):
    """실제 배치 사이클에서 신규 매수만 게이트가 막고, 신호 상태·청산은 건드리지 않는다"""

    BLOCKED = (3.11, 3.10, 3.15, 3.08, 3.20, 3.12)      # 5개 전 3.11 → 3.12: 상승 → 차단 (SNOW 2026-10-12 형)
    ALLOWED = (3.40, 3.32, 3.48, 3.42, 3.34, 3.30)      # 5개 전 3.40 → 3.30: 하락 → 허용

    async def put(self, values):
        end = date.today() - timedelta(days=1)
        await cg.store_observations(self.redis, series(*values, end=end))

    async def test_blocked_gate_prevents_entry_and_keeps_signal_waiting(self):
        await self.put(self.BLOCKED)
        with self.assertLogs(batch.logger, level="INFO") as logs:
            await self.run_cycles(4)
        self.assertEqual(len(Market.buys()), 0, "차단 상태인데 매수했다")
        self.assertEqual(self.swing.SIGNAL, 0)
        self.assertTrue(any("신용 스프레드 게이트" in m and "차단" in m for m in logs.output), "차단 사유가 로그에 없다")

    async def test_allowed_gate_enters_as_usual(self):
        await self.put(self.ALLOWED)
        await self.enter_position()
        self.assertEqual(len(Market.buys()), 1)

    async def test_blocked_then_allowed_enters_on_next_cycle_with_same_signal(self):
        """막혀 있던 동안 쌓인 신호 상태가 그대로라서, 허용으로 바뀐 첫 사이클에 바로 진입한다 (백테스트의 '다음 날 허용' 동작)"""
        await self.put(self.BLOCKED)
        await self.run_cycles(3)
        self.assertEqual(len(Market.buys()), 0)
        await self.put(self.ALLOWED)
        await self.run_cycle()
        self.assertEqual(len(Market.buys()), 1, "허용으로 바뀌었는데 같은 사이클에 진입하지 못했다")
        self.assertEqual(self.swing.SIGNAL, 1)

    async def test_disabled_gate_does_not_block(self):
        await self.put(self.BLOCKED)
        with patch.object(cg, "enabled", return_value=False):
            await self.enter_position()
        self.assertEqual(len(Market.buys()), 1)

    async def test_no_data_does_not_block(self):
        """Redis 에 값이 없으면(첫 기동·FRED 장애 직후) 평소처럼 매수"""
        self.assertNotIn(cg.REDIS_KEY, self.redis.store)
        await self.enter_position()

    async def test_blocked_gate_does_not_block_exit(self):
        """보유 중 청산은 게이트와 무관 — 차단 상태에서도 청산선을 이탈하면 판다"""
        await self.put(self.ALLOWED)
        await self.enter_position()
        await self.put(self.BLOCKED)
        Market.move(80.0)                      # 청산선 크게 아래
        await self.run_cycle()
        self.assertGreaterEqual(len(Market.sells()), 1, "차단 상태에서 청산이 막혔다")


if __name__ == "__main__":
    unittest.main()
