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

2026-09-23 갱신 — 제한기가 "호출 간 최소 간격"에서 "슬라이딩 창 안의 건수"로 바뀌었다.
간격 방식은 KIS 의 계산 축(1초 창 내 건수)과 달라 양쪽으로 어긋났다: 창 경계에서
세 건이 겹치는 것을 못 막으면서(모의 서버에서 실제로 초과 발생), KIS 가 허용하는
연속 호출은 불필요하게 막았다. 그래서 이 파일도 "간격이 벌어지는가"가 아니라
"어떤 1초 창에도 한도를 넘지 않는가"를 고정한다.
"""
import asyncio
import time
import unittest

from app.external import http_client
from app.external.rate_limiter import KeyedRateLimiter


class RateLimiterTest(unittest.IsolatedAsyncioTestCase):

    KIS_WINDOW = 1.0   # KIS 가 건수를 세는 창

    async def _marks(self, limiter, key, limit, n):
        """key 로 n건 동시 요청 → 각 호출이 나간 시각(초)"""
        start = time.perf_counter()
        marks = []

        async def call():
            await limiter.acquire(key, limit)
            marks.append(time.perf_counter() - start)

        await asyncio.gather(*[call() for _ in range(n)])
        return sorted(marks)

    def _max_in_window(self, marks):
        """어떤 KIS 1초 구간에 최대 몇 건이 몰렸는지"""
        return max(
            sum(1 for u in marks[i:] if u < t + self.KIS_WINDOW)
            for i, t in enumerate(marks)
        )

    async def test_no_window_exceeds_limit(self):
        """어떤 1초 창에도 한도를 넘지 않는다

        이것이 KIS 가 실제로 세는 축이다. 간격 방식은 '보낸 시각' 기준으로만
        한도를 지켜, 도착 시각이 조금 압축되면 한 창에 한도+1 건이 들어갔다.
        """
        limiter = KeyedRateLimiter()

        marks = await self._marks(limiter, "appkey-A", 2.0, 4)

        self.assertLessEqual(
            self._max_in_window(marks), 2,
            f"1초 창에 한도를 넘는 호출이 몰렸다: {marks}"
        )

    async def test_burst_within_window_is_allowed(self):
        """한도만큼은 즉시 나간다 (KIS 가 허용하는 것을 막지 않는다)

        간격 방식은 한도가 2건이어도 두 번째 호출을 0.5초 대기시켰다.
        사용자 화면 하나가 여러 건을 조회하면 이유 없이 느려진다.
        """
        limiter = KeyedRateLimiter()

        marks = await self._marks(limiter, "appkey-A", 2.0, 2)

        self.assertLess(marks[-1], 0.1, f"창에 여유가 있는데 대기했다: {marks}")

    async def test_penalize_pauses_only_that_key(self):
        """서버가 '초과'로 답하면 그 앱키 전체가 함께 물러난다

        재시도하는 코루틴만 쉬면 같은 앱키를 쓰는 다른 호출이 계속 나가 집계
        속도가 안 떨어진다 — 백오프가 '누가 거절당하는지'만 바꾸고 재시도까지
        연달아 초과당한다. 다른 앱키는 무관하므로 영향받으면 안 된다.
        """
        limiter = KeyedRateLimiter()
        limiter.penalize("appkey-A", 0.3)
        start = time.perf_counter()

        await asyncio.gather(
            limiter.acquire("appkey-A", 2.0),
            limiter.acquire("appkey-B", 2.0),
        )
        elapsed_both = time.perf_counter() - start

        self.assertGreaterEqual(elapsed_both, 0.25, "페널티를 받은 키가 대기하지 않았다")

        start = time.perf_counter()
        await limiter.acquire("appkey-B", 2.0)
        self.assertLess(time.perf_counter() - start, 0.1, "무관한 앱키까지 멈췄다")

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

    def test_batch_end_log_reports_failures_and_cycle_usage(self):
        """마감 로그가 실패 건수와 주기 사용률을 말한다

        해외 배치(us_trade_job)는 `not isinstance(r, Exception)` 으로 세고 있었다.
        process_single_swing 은 예외를 삼키고 RESULT_FAILED 문자열을 돌려주므로
        실패가 전부 성공으로 집계됐다 — 국내 경로만 고쳐지고 해외가 누락된
        상태였다. 두 경로가 같은 집계를 쓰는지 여기서 고정한다.

        소요 시간을 함께 남기는 이유: 주기를 넘기면 APScheduler 가
        (max_instances=1) 다음 트리거를 스킵해 매매 사이클이 조용히 사라진다.
        """
        from app.domain.swing.trading import auto_swing_batch as batch

        results = [batch.RESULT_OK] * 5 + [batch.RESULT_FAILED] * 3

        with self.assertLogs(batch.__name__, level="INFO") as caught:
            batch._log_batch_end("US BATCH", results, elapsed=12.3)

        done = next(m for m in caught.output if "배치 작업 완료" in m)
        self.assertIn("성공: 5", done)
        self.assertIn("실패: 3", done)       # 예외가 아니어도 실패로 센다
        self.assertIn("소요: 12.3초", done)

        # 주기의 절반을 넘으면 동시 실행 상한을 되돌아보라고 경고한다
        with self.assertLogs(batch.__name__, level="WARNING") as caught:
            batch._log_batch_end("BATCH", [batch.RESULT_OK], elapsed=batch._CYCLE_SEC * 0.7)

        self.assertTrue(
            any("_SEMAPHORE" in m for m in caught.output),
            f"주기 초과 경고가 없다: {caught.output}",
        )


if __name__ == "__main__":
    unittest.main()
