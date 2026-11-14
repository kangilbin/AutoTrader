"""
신용 스프레드 진입 게이트 (Q3) — 신규 매수 전에 시장의 위험 선호가 회복 중인지 본다

규칙: 미국 하이일드 스프레드(BAMLH0A0HYM2)의 가장 최근 값이
  ① LOOKBACK 개 관측치 전보다 낮고(스프레드 하락 = 신용 위험 완화 중)
  ② MIN_LEVEL(%) 이상(지나치게 평온한 구간은 이 전략이 잘 안 먹혔다)
일 때만 신규 매수를 허용한다. 미국·국내 종목에 같은 값을 쓴다 — 국내 주식은 국내 회사채 스프레드(BBB-)보다
글로벌 위험 선호에 더 민감하게 움직였고(KODEX200과 일변화 상관 −0.30 대 +0.08), 국내 스프레드 규칙은 수익을 크게 깎았다.

근거 (2026-11-14 백테스트, 현재 전략 대비 · 모두 FRED 3년 실데이터 한 사이클 안의 결과):
- 3년 창 합R +54%, 풀 제한 계좌 국내 +55%→+68%(낙폭 30%→13.5%), 미국 +42%→+50%(낙폭 20%→13%)
- 큰 하락 뒤(낙폭 ≤ −30%) 진입의 평균 R +0.72 → +1.71
한계 (코드를 바꾸기 전에 알아 둘 것):
- 3년 한 사이클에서 만든 규칙이다. VIX·국내 스프레드·장기 대용 지표(Baa, HYG/LQD)로는 재현되지 않았고, 2022년 같은 지속 하락장은 검증하지 못했다.
- 진입을 막는 규칙이라 막힌 구간에서 수익이 날 수도 있다(3년 중 허용되는 날은 약 32%). 그림자 모드 없이 바로 적용하므로 HY_OAS_GATE_ENABLED 로 끌 수 있게 했다.
- 청산에는 영향이 없다. 보유 중인 포지션은 그대로 청산선을 따른다.
- 스프레드가 하루 단위로 흔들려서, 차단된 날 다음 날 허용으로 바뀌고 신호가 유지되면 그날 진입한다(첫 진입을 하루 미루는 효과에 그칠 수 있다).
- 같은 종목의 두 번째 진입처럼 허용 상태에서 오는 진입은 막지 못한다(SNOW 2026-10-28).

안전장치(fail-open): 데이터가 없거나, 관측치가 부족하거나, STALE_DAYS 넘게 갱신되지 않았거나, Redis 가 실패하면 허용한다 —
이 게이트가 고장 나서 매매가 멈추는 쪽이 더 나쁘다.

순수 판정 함수(decide)와 그 판정이 쓰는 Redis 상태 헬퍼를 둔다. 주문은 호출부가 다룬다.
"""
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import List, Optional, Tuple

from app.core.config import get_settings
from app.external import fred_api

logger = logging.getLogger(__name__)

SERIES_ID = "BAMLH0A0HYM2"
LOOKBACK = 5            # 비교 기준: 이 개수 전 관측치 (백테스트의 5거래일 변화)
MIN_LEVEL = 2.9         # 이 값(%) 이상일 때만 허용
STALE_DAYS = 7          # 가장 최근 관측일이 이보다 오래되면 판단하지 않고 허용 (주말·휴장 여유)
HISTORY_KEEP = 30       # Redis 에 남기는 관측치 개수
REDIS_KEY = "credit_gate:hy_oas"
REDIS_TTL = 14 * 86400  # 갱신이 두 주 끊기면 키가 사라져 fail-open 으로 돌아간다


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str                       # 로그용 사유
    level: Optional[float] = None     # 가장 최근 스프레드(%)
    prior: Optional[float] = None     # LOOKBACK 개 전 스프레드(%)


def enabled() -> bool:
    return bool(get_settings().HY_OAS_GATE_ENABLED)


def decide(observations: List[Tuple[date, float]], today: date) -> GateDecision:
    """진입 허용 여부 (순수 함수) — observations 는 날짜 오름차순

    데이터가 부족하거나 낡았으면 허용한다(fail-open). 그렇지 않으면 ①하락 중 ②수준 조건을 모두 만족할 때만 허용한다.
    """
    if len(observations) < LOOKBACK + 1:
        return GateDecision(True, f"관측치 부족({len(observations)}개) → 허용")
    last_day, level = observations[-1]
    age = (today - last_day).days
    if age > STALE_DAYS:
        return GateDecision(True, f"데이터 {age}일 전({last_day}) → 판단 불가, 허용", level)
    prior = observations[-1 - LOOKBACK][1]
    if level >= prior:
        return GateDecision(False, f"스프레드 상승·정체 중 ({prior:.2f}→{level:.2f}%) → 신규 매수 차단", level, prior)
    if level < MIN_LEVEL:
        return GateDecision(False, f"스프레드 {level:.2f}% < {MIN_LEVEL}% (평온 구간) → 신규 매수 차단", level, prior)
    return GateDecision(True, f"스프레드 하락 중 ({prior:.2f}→{level:.2f}%) → 허용", level, prior)


# ==================== Redis 상태 (최근 관측치) ====================

async def store_observations(redis_client, observations: List[Tuple[date, float]]) -> None:
    payload = [[d.isoformat(), v] for d, v in observations[-HISTORY_KEEP:]]
    await redis_client.setex(REDIS_KEY, REDIS_TTL, json.dumps(payload))


async def load_observations(redis_client) -> List[Tuple[date, float]]:
    """저장된 관측치 — 없거나 깨졌으면 빈 목록"""
    raw = await redis_client.get(REDIS_KEY)
    if not raw:
        return []
    try:
        return [(datetime.strptime(d, "%Y-%m-%d").date(), float(v)) for d, v in json.loads(raw)]
    except (ValueError, TypeError):
        return []


async def refresh(redis_client) -> int:
    """FRED 에서 최근 값을 받아 Redis 에 저장 — 저장한 관측치 수. 실패는 호출부가 처리한다(기존 값은 그대로 남는다)"""
    observations = await fred_api.get_series(SERIES_ID)
    await store_observations(redis_client, observations)
    return min(len(observations), HISTORY_KEEP)


async def check(redis_client, today: Optional[date] = None) -> GateDecision:
    """신규 매수 직전에 부르는 진입 — 어떤 오류도 매매를 막지 않고 허용으로 돌려준다"""
    if not enabled():
        return GateDecision(True, "게이트 비활성")
    try:
        observations = await load_observations(redis_client)
    except Exception as e:
        logger.warning(f"신용 스프레드 게이트: Redis 조회 실패 → 허용: {e}")
        return GateDecision(True, "Redis 오류 → 허용")
    return decide(observations, today or datetime.now(ZoneInfo("America/New_York")).date())
