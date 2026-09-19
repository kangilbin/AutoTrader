"""
KIS 유량 제한 / 배치 집계 회귀 테스트

실행:
  uv run pytest tests/test_kis_rate_limit.py -v

운영 장애(2026-09-18 06:00~06:20)를 고정한다. 한 사용자의 스윙 5건이 같은 앱키로
동시에 시세를 조회 → KIS 유량 초과 → KIS가 응답을 7~10초 지연 후 500 → 10초 타임아웃
→ 전 종목 매매 판단 불가. 그런데 배치 로그는 "성공: 5, 실패: 0"을 찍어 장애가 은폐됐다.

핵심 교훈 두 가지를 각각 테스트로 고정한다.
  1) 유량 제한은 앱키 단위다. 전역 Semaphore(동시 N개)로는 초당 건수를 못 막는다.
  2) 예외를 삼키는 워커의 결과는 반환값으로 구분해야 집계가 진실을 말한다.
"""
import asyncio
import time
import unittest

from app.external import http_client
from app.external.rate_limiter import KeyedRateLimiter


class RateLimiterTest(unittest.IsolatedAsyncioTestCase):

    async def test_same_key_calls_are_spaced(self):
        """같은 앱키 호출은 1/rate 간격으로 줄을 선다"""
        limiter = KeyedRateLimiter()
        start = time.perf_counter()
        marks = []

        async def call():
            await limiter.acquire("appkey-A", 2.0)      # 초당 2건
            marks.append(time.perf_counter() - start)

        await asyncio.gather(*[call() for _ in range(3)])

        marks.sort()
        gaps = [round(b - a, 1) for a, b in zip(marks, marks[1:])]
        self.assertEqual(gaps, [0.5, 0.5], f"호출 간격이 벌어지지 않았다: {marks}")

    async def test_different_keys_do_not_block_each_other(self):
        """다른 앱키는 서로 막지 않는다

        전역 제한으로 만들면 무관한 사용자끼리 대기하게 되어 사용자가 늘수록
        배치 처리량이 급감한다.
        """
        limiter = KeyedRateLimiter()
        start = time.perf_counter()

        await asyncio.gather(*[limiter.acquire(f"appkey-{i}", 2.0) for i in range(5)])

        self.assertLess(time.perf_counter() - start, 0.1, "다른 앱키끼리 서로 대기했다")

    async def test_zero_rate_is_noop(self):
        """제한이 꺼져 있으면 대기하지 않는다 (설정으로 무력화 가능)"""
        limiter = KeyedRateLimiter()
        start = time.perf_counter()

        await limiter.acquire("appkey-A", 0)

        self.assertLess(time.perf_counter() - start, 0.05)


class ThrottleRoutingTest(unittest.IsolatedAsyncioTestCase):
    """fetch가 어떤 키/한도로 제한을 거는지"""

    async def asyncSetUp(self):
        self.calls = []
        self._orig = http_client.kis_rate_limiter

        class SpyLimiter:
            async def acquire(self_inner, key, rate):
                self.calls.append((key, rate))

        http_client.kis_rate_limiter = SpyLimiter()

    async def asyncTearDown(self):
        http_client.kis_rate_limiter = self._orig

    async def test_simulation_and_real_have_separate_limits(self):
        """모의투자 서버는 실전보다 낮은 한도를 쓴다"""
        from app.core.config import get_settings

        settings = get_settings()
        await http_client._throttle(
            f"{settings.DEV_API_URL}/uapi/x", {"headers": {"appkey": "K1"}}
        )
        await http_client._throttle(
            f"{settings.REAL_API_URL}/uapi/x", {"headers": {"appkey": "K1"}}
        )

        sim_rate, real_rate = self.calls[0][1], self.calls[1][1]
        self.assertEqual(sim_rate, settings.KIS_RATE_LIMIT_SIM)
        self.assertEqual(real_rate, settings.KIS_RATE_LIMIT_REAL)
        self.assertLess(sim_rate, real_rate, "모의 한도가 실전보다 낮아야 한다")

    async def test_key_is_appkey_not_url(self):
        """제한 키는 앱키다 (사용자별 독립 보장의 근거)"""
        await http_client._throttle("https://x/uapi", {"headers": {"appkey": "K-USER-A"}})

        self.assertEqual(self.calls[0][0], "K-USER-A")

    async def test_call_without_appkey_is_not_throttled(self):
        """토큰 발급 등 appkey 헤더가 없는 호출은 건드리지 않는다

        토큰 발급은 '1분 1회'라는 다른 제한을 받으며 oauth_token이 락으로 따로 다룬다.
        """
        await http_client._throttle("https://x/oauth2/tokenP", {"json": {"appkey": "K1"}})

        self.assertEqual(self.calls, [])


class BatchSummaryTest(unittest.TestCase):
    """배치 집계가 실패를 실패로 세는지

    process_single_swing은 예외를 내부에서 삼키고 정상 반환한다. 반환값으로 구분하지
    않으면 gather는 예외를 보지 못해 전부 실패해도 '성공'으로 집계된다 — 운영에서
    매매가 5분마다 멈췄는데 로그는 "성공: 5, 실패: 0"이었다.
    """

    def test_counts_reflect_actual_outcomes(self):
        from app.domain.swing.trading import auto_swing_batch as batch

        results = [
            batch.RESULT_OK,
            batch.RESULT_FAILED,
            batch.RESULT_SKIPPED,
            RuntimeError("워커 밖에서 터진 예외"),
            None,                      # 명시 반환 없이 끝난 정상 경로
        ]

        summary = batch._summarize(results)

        self.assertEqual(summary, {"ok": 2, "failed": 2, "skipped": 1})


if __name__ == "__main__":
    unittest.main()
