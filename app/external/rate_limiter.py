"""
외부 API 호출 속도 제한

KIS 유량 제한은 **앱키 단위**다. 전역 제한을 걸면 서로 무관한 사용자끼리 대기하게 되고
(사용자가 늘수록 처리량이 급감), 반대로 한 사용자가 스윙을 여러 개 돌리면 전역 제한이
느슨해 유량을 초과한다. 그래서 앱키를 키로 삼는다.

동시 실행 수 제한(Semaphore)으로는 이 문제를 풀 수 없다 — "동시 5개"는 "초당 5건 이하"를
보장하지 않는다. 호출 간 최소 간격을 강제해야 한다.
"""
import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class KeyedRateLimiter:
    """키(앱키)별 최소 호출 간격 보장

    같은 키의 호출은 직렬화되어 1/rate 초 간격으로 나가고, 다른 키끼리는 서로 막지 않는다.
    """

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._next_at: dict[str, float] = {}

    async def acquire(self, key: str, rate_per_sec: float) -> None:
        """이 키의 다음 호출 차례가 될 때까지 대기"""
        if not key or rate_per_sec <= 0:
            return

        interval = 1.0 / rate_per_sec
        loop = asyncio.get_running_loop()

        # 락을 쥔 채 대기해야 같은 키의 호출이 간격을 두고 줄을 선다.
        # 락 밖에서 자면 여러 코루틴이 같은 시각을 보고 동시에 출발한다.
        async with self._locks[key]:
            now = loop.time()
            next_at = self._next_at.get(key, 0.0)
            wait = next_at - now
            if wait > 0:
                if wait > interval:
                    logger.debug(f"유량 제한 대기 {wait:.2f}s (key={key[:8]}…)")
                await asyncio.sleep(wait)
                now = loop.time()
            self._next_at[key] = now + interval


kis_rate_limiter = KeyedRateLimiter()
