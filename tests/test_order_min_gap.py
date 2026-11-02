"""주문 POST 직전 최소 간격 — 모의서버 전용

실행: uv run pytest tests/test_order_min_gap.py -v
모의서버는 직전 요청 0.6초 뒤의 주문 POST 를 유량초과로 거절했다 (2026-11-02 실측, 2초 간격은 통과).
실전은 한도가 넉넉하고 증상이 없어 적용하지 않는다.
"""
import time
import unittest
from unittest.mock import AsyncMock, patch

from app.core.config import get_settings
from app.external import http_client
from app.external.rate_limiter import KeyedRateLimiter


class LimiterMinGapTest(unittest.IsolatedAsyncioTestCase):
    async def test_waits_until_gap_since_previous_call(self):
        limiter = KeyedRateLimiter()
        await limiter.acquire("k", 10)
        t = time.perf_counter()
        await limiter.acquire("k", 10, min_gap=0.3)
        self.assertGreaterEqual(time.perf_counter() - t, 0.25)

    async def test_no_wait_without_gap(self):
        limiter = KeyedRateLimiter()
        await limiter.acquire("k", 10)
        t = time.perf_counter()
        await limiter.acquire("k", 10)
        self.assertLess(time.perf_counter() - t, 0.1)

    async def test_gap_is_per_key(self):
        limiter = KeyedRateLimiter()
        await limiter.acquire("a", 10)
        t = time.perf_counter()
        await limiter.acquire("b", 10, min_gap=0.3)
        self.assertLess(time.perf_counter() - t, 0.1)


class ThrottleOrderGapTest(unittest.IsolatedAsyncioTestCase):
    async def _gap_passed(self, url: str, order: bool) -> float:
        with patch.object(http_client.kis_rate_limiter, "acquire", new=AsyncMock()) as acquire:
            await http_client._throttle(url, {"headers": {"appkey": "key"}}, order)
        return acquire.await_args.kwargs["min_gap"]

    async def test_simulation_order_gets_gap(self):
        url = f"{get_settings().DEV_API_URL}/uapi/overseas-stock/v1/trading/order"
        self.assertEqual(await self._gap_passed(url, True), http_client.ORDER_MIN_GAP_SEC)

    async def test_simulation_query_has_no_gap(self):
        url = f"{get_settings().DEV_API_URL}/uapi/overseas-stock/v1/quotations/inquire-asking-price"
        self.assertEqual(await self._gap_passed(url, False), 0.0)

    async def test_real_order_has_no_gap(self):
        url = f"{get_settings().REAL_API_URL}/uapi/overseas-stock/v1/trading/order"
        self.assertEqual(await self._gap_passed(url, True), 0.0)


if __name__ == "__main__":
    unittest.main()
