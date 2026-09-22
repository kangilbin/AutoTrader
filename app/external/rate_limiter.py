"""
외부 API 호출 속도 제한

KIS 유량 제한은 **앱키 단위**다. 전역 제한을 걸면 서로 무관한 사용자끼리 대기하게 되고
(사용자가 늘수록 처리량이 급감), 반대로 한 사용자가 스윙을 여러 개 돌리면 전역 제한이
느슨해 유량을 초과한다. 그래서 앱키를 키로 삼는다.

동시 실행 수 제한(Semaphore)으로는 이 문제를 풀 수 없다 — "동시 5개"는 "초당 5건 이하"를
보장하지 않는다.

**계산 축을 KIS 와 맞춘다.** KIS 는 "1초 창 안의 건수"로 센다. 그래서 여기서도 창으로
센다. 이전 구현은 "호출 간 최소 간격(1/rate)"을 벌리는 방식이었는데, 축이 달라 양쪽으로
어긋났다:

  - **막아야 할 걸 못 막았다.** 간격 0.555초로 0 / 0.555 / 1.110 에 보내면 *보낸* 시각
    기준으로는 어떤 1초 창에도 2건이다. 그러나 도착 시각이 0.1초만 압축되면 세 건이 KIS
    쪽 한 창에 들어간다. 인터넷 구간에서 0.1초 지터는 흔하고, 실제로 모의 서버에서
    "초당 거래건수를 초과" 가 났다.
  - **막을 필요 없는 걸 막았다.** KIS 가 허용하는 '연속 2건'을 간격 방식은 금지한다.
    화면 하나에서 4건을 조회하면 이유 없이 2.2초가 걸렸다.

안전 마진은 허용 건수를 깎아서가 아니라 **창을 KIS 의 1초보다 넓게 잡아서** 확보한다.
그래야 창 안에서의 버스트(응답성)를 유지하면서 도착 지터를 흡수한다.
"""
import asyncio
import logging
from collections import defaultdict, deque

logger = logging.getLogger(__name__)

# KIS 는 1초 창으로 세지만 우리는 이보다 넓은 창으로 센다.
# 차이(0.3초)가 도착 지터를 흡수하는 마진이다 — 우리 창의 마지막 건과 다음 창의 첫 건이
# KIS 쪽에서 같은 1초에 겹치려면 지터가 이 차이보다 커야 한다.
WINDOW_SEC = 1.3


class KeyedRateLimiter:
    """키(앱키)별 유량 제한 — 슬라이딩 창 안의 호출 건수를 센다

    같은 키의 호출은 창 한도 안에서 줄을 서고, 다른 키끼리는 서로 막지 않는다.
    """

    def __init__(self, window: float = WINDOW_SEC):
        self._window = window
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._sent: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}

    async def acquire(self, key: str, limit_per_window: float) -> None:
        """창 한도에 여유가 생길 때까지 대기한 뒤 호출 1건을 기록한다

        Args:
            key: 유량 한도의 축 (KIS 앱키)
            limit_per_window: 창 안에서 허용할 호출 건수 (KIS 한도)
        """
        if not key or limit_per_window <= 0:
            return

        limit = max(1, int(limit_per_window))
        loop = asyncio.get_running_loop()

        # 락을 쥔 채 대기해야 같은 키의 호출이 줄을 선다.
        # 락 밖에서 자면 여러 코루틴이 같은 창 상태를 보고 동시에 출발해 한도를 넘긴다.
        async with self._locks[key]:
            while True:
                now = loop.time()

                # 서버가 직접 '초과'라고 답한 구간은 창 계산보다 우선한다 (penalize 참고).
                blocked = self._blocked_until.get(key, 0.0)
                if blocked > now:
                    await asyncio.sleep(blocked - now)
                    continue

                sent = self._sent[key]
                while sent and sent[0] <= now - self._window:
                    sent.popleft()

                if len(sent) < limit:
                    sent.append(now)
                    return

                # 창이 꽉 찼다 — 가장 오래된 건이 창 밖으로 빠지는 시점까지 대기
                wait = sent[0] + self._window - now
                logger.debug(
                    f"유량 창 대기 {wait:.2f}s (key={key[:8]}…, {len(sent)}/{limit})"
                )
                await asyncio.sleep(wait)

    def penalize(self, key: str, seconds: float) -> None:
        """서버가 '초과'로 답했을 때 같은 키를 쓰는 호출 전체를 잠시 멈춘다

        재시도하는 코루틴만 쉬어서는 집계 속도가 떨어지지 않는다 — 같은 앱키를 쓰는 다른
        호출이 그 사이에도 계속 나가기 때문이다. 실제로 배치가 스윙 N건을 동시에 돌리다
        한 건이 초과를 맞으면, 나머지가 계속 밀어넣어 재시도까지 연달아 초과당했다.
        (백오프가 '누가 거절당하는지'만 바꾸고 총 요청 속도는 그대로였다.)

        창 계산이 맞다면 이 경로는 안 타는 것이 정상이다. 우리가 모르는 이유 —
        KIS 정책 변경, 같은 앱키를 쓰는 다른 클라이언트 — 로 초과가 났을 때를 위한
        안전망이라, 창 폭을 조정하는 대신 일시 정지로만 대응한다.
        """
        if not key or seconds <= 0:
            return

        until = asyncio.get_running_loop().time() + seconds
        self._blocked_until[key] = max(self._blocked_until.get(key, 0.0), until)


kis_rate_limiter = KeyedRateLimiter()
