"""
종가 확인 매매 — 장중 흔들림에 털리는 손절과 장중 돌파 실패 재진입을 줄인다

1) 손절: 마지막 배치(종가 근처, 마지막 두 번 실행)에서 현재가 ≤ 청산선이면 다음 거래일 배치에서 매도한다.
   장중 터치만으로는 팔지 않고, 큰 폭락(청산선 − SAFETY_NET_ATR × ATR 이하)만 즉시 판다 (안전망).
2) 재진입: 직전 전량 청산 후 REENTRY_WINDOW_DAYS 일 이내의 재매수는 마지막 배치(마지막 두 번 실행)에서만 한다.
   첫 진입은 지금처럼 장중 실시간이다.

근거 (2026-11-10 일봉 백테스트 189종목 · 체결 불이익 0.7% · 현재 방식 근사 대비 평균 R +0.235 → +0.470):
- 청산선에 닿아 판 거래의 54%가 그날 종가에서는 매도가보다 위였다 — 장중 터치 매도는 흔들림에 자주 털린다.
  (FOUR 2026-11-05: 시장 급락일 10:05 청산선 이탈로 매도, 같은 날 종가는 청산선 +7.7%)
- 장중에 EMA20 위로 올라 재진입했다가 종가에서 깨지는 경우가 있다 (SNOW 2026-10-28).
한계 (코드를 바꾸기 전에 알아 둘 것):
- 손실 거래 평균이 −1.15R → −1.34R로 커진다 (갭하락 때 다음 날에야 판다). 안전망은 −3R 이하 꼬리만 줄인다.
- 이득은 강한 추세 해(2020·2023·2025)에 몰리고 정체·약세 해에는 없었다. 홀드아웃(2015~2019)은 아직 쓰지 않았다.
- 일봉으로 근사한 값이라 장중 실시간 동작의 실제 효과는 운영 데이터로 확인해야 한다.

마지막 배치를 한 번이 아니라 마지막 두 번 실행으로 잡은 이유:
- 한 번만 보면 그 실행이 지연·실패(Redis 오류, KIS 지연 등)했을 때 그날 확인이 통째로 빠진다.
- 매수의 '2회 연속 충족' 확인이 두 번 실행으로 채워진다.
- 국내는 15:20 이 동시호가(체결은 15:30)라 마지막 실행을 15:15 로 본다.
예약은 마지막 배치 구간의 실행마다 갱신하고 마지막 값이 최종이다.
캘린더가 없어 휴장·조기폐장일에는 마지막 배치 시각이 어긋난다 (기존 is_market_open 도 마찬가지).
알려진 한계:
- 마지막 두 실행이 모두 밀리거나(APScheduler max_instances=1, misfire_grace_time=1) 체결 미확인 주문 때문에 청산 평가를 건너뛰면 그날 확인이 빠진다.
- Redis 상태는 DB 커밋보다 먼저 기록된다. 커밋이 실패하면 일시적으로 어긋나지만 다음 사이클의 거부 후 실보유 대조가 복구한다.
- 모드를 껐다 켜면 보유 중 남은 예약(최대 PENDING_EXIT_TTL)이 첫 배치에 실행된다.
- 포지션이 없는 사이클은 낡은 예약을 지운다 (auto_swing_batch.process_single_swing) — 새 포지션이 이전 예약을 물려받지 않게 하는 단일 방어선이다.

순수 판정 함수(시각, 판정 규칙)와 그 판정이 쓰는 Redis 상태 헬퍼(예약·마지막 청산일)를 둔다. 주문은 호출부가 다룬다.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from typing import Optional
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.core.market_code import is_overseas
from . import loss_guard

logger = logging.getLogger(__name__)

SAFETY_NET_ATR = 1.0          # 장중 즉시 매도 안전망: 현재가 ≤ 청산선 − 1 × ATR(전일 완성봉)
REENTRY_WINDOW_DAYS = 30      # 직전 전량 청산 후 이 기간 안의 재매수는 마지막 배치에서만

PENDING_EXIT_TTL = 10 * 86400  # 매도 예약(다음 거래일 배치에 실행) 보관 — 긴 연휴(추석·설 등)를 넘겨도 남게
LAST_EXIT_TTL = 40 * 86400    # 재진입 판정용 마지막 청산일 보관 (30일 + 여유)

# 마지막 배치 — 스케줄러(scheduler.py)의 마지막 실행 시각과 같아야 한다.
#   미국: 10~15시 5분마다 → 15:50·15:55 ET 가 마지막 두 번
#   국내: 15시대 0,5,10,15,20분 → 15:10·15:15 KST (15:20 은 종가 동시호가라 제외)
_FINAL_BATCH = {
    "US": (dt_time(15, 50), dt_time(16, 0), "America/New_York"),
    "KR": (dt_time(15, 10), dt_time(15, 20), "Asia/Seoul"),
}

SELL = "SELL"
HOLD = "HOLD"
REASON_SAFETY_NET = "안전망"
REASON_NEXT_DAY = "종가 확인 후 매도"


def enabled() -> bool:
    """종가 확인 모드 사용 여부 — 정규장 밖 테스트 모드(ALLOW_OFFHOURS_TRADING)에서는 마지막 배치가 없어 쓰지 않는다"""
    settings = get_settings()
    return bool(settings.CLOSE_CONFIRM_ENABLED) and not settings.ALLOW_OFFHOURS_TRADING


def _group(mrkt_code: str) -> str:
    return "US" if is_overseas(mrkt_code) else "KR"


def market_now(mrkt_code: str, now: Optional[datetime] = None) -> datetime:
    """그 시장 현지 시각"""
    tz = ZoneInfo(_FINAL_BATCH[_group(mrkt_code)][2])
    return now.astimezone(tz) if now else datetime.now(tz)


def trading_date(mrkt_code: str, now: Optional[datetime] = None) -> date:
    """그 시장 현지 날짜 — 매도 예약·마지막 청산일의 기준"""
    return market_now(mrkt_code, now).date()


def is_final_batch(mrkt_code: str, now: Optional[datetime] = None) -> bool:
    """지금이 그 시장의 마지막 배치(종가 확인) 구간인가 — 잡 시작 시각으로 한 번만 판정해 종목들에 같이 넘긴다.

    종목 처리가 길어져 구간 끝을 넘겨도 같은 배치 안에서는 판정이 바뀌지 않게 하려는 것이다.
    """
    start, end, _ = _FINAL_BATCH[_group(mrkt_code)]
    local = market_now(mrkt_code, now)
    if local.weekday() >= 5:
        return False
    return start <= local.time() < end


@dataclass(frozen=True)
class ExitDecision:
    action: str                       # SELL | HOLD
    reason: str = ""                  # SELL 일 때 매도 사유 꼬리표 (안전망 / 종가 확인 후 매도)
    pending: Optional[bool] = None    # True: 매도 예약(다음 거래일 실행), False: 예약 해제, None: 변경 없음


def decide_exit(
    price: float, exit_line: float, atr: float, *,
    final_batch: bool, pending_date: Optional[date], today: date,
) -> ExitDecision:
    """보유 중 종가 확인 손절 판정 (순수 함수)

    SELL 일 때는 예약 상태를 바꾸지 않는다 — 매도가 부분 체결·실패해도 다음 배치가 같은 판정을 이어가야 하므로,
    예약은 매도가 끝난 뒤 호출부가 지운다.

    1) 안전망: 현재가 ≤ 청산선 − SAFETY_NET_ATR × ATR → 장중 어느 때든 즉시 매도
    2) 이전 거래일의 마지막 배치에서 청산선 이탈이 확인돼 예약돼 있으면 → 오늘 첫 배치에서 매도 (오늘 가격이 회복했어도)
    3) 오늘이 마지막 배치 구간이면: 현재가 ≤ 청산선 → 매도 예약 / 청산선 위 → 예약 해제 (구간의 마지막 값이 최종)
    4) 그 외 장중에는 청산선을 건드려도 보유 (예약 상태 유지)
    """
    if atr > 0 and price <= exit_line - SAFETY_NET_ATR * atr:
        return ExitDecision(SELL, REASON_SAFETY_NET)
    if pending_date is not None and pending_date < today:
        return ExitDecision(SELL, REASON_NEXT_DAY)
    if final_batch:
        return ExitDecision(HOLD, pending=price <= exit_line)
    return ExitDecision(HOLD)


# ==================== Redis 상태 (매도 예약 · 마지막 청산일) ====================

def pending_key(swing_id: int) -> str:
    return f"pending_exit:{swing_id}"


def last_exit_key(swing_id: int) -> str:
    return f"last_exit:{swing_id}"


def _parse_date(raw) -> Optional[date]:
    """Redis 값(bytes/str) → 날짜. 없거나 깨진 값은 None (예약 없음·첫 진입으로 본다)"""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except ValueError:
        return None


async def get_pending(redis_client, swing_id: int) -> Optional[date]:
    """매도 예약일 (청산선 이탈이 확인된 마지막 배치의 날짜)"""
    return _parse_date(await redis_client.get(pending_key(swing_id)))


async def set_pending(redis_client, swing_id: int, day: date) -> None:
    await redis_client.setex(pending_key(swing_id), PENDING_EXIT_TTL, day.isoformat())


async def clear_pending(redis_client, swing_id: int) -> None:
    await redis_client.delete(pending_key(swing_id))


async def discard_pending(redis_client, swing_id: int) -> None:
    """새 포지션을 잡을 때 남아 있는 예약을 버린다 (청산 경로가 놓친 경우의 이중 방어) — Redis 오류는 삼킨다"""
    try:
        await clear_pending(redis_client, swing_id)
    except Exception as e:
        logger.warning(f"[SWING_ID={swing_id}] 매도 예약 정리(Redis) 실패: {e}")


async def get_last_exit(redis_client, swing_id: int) -> Optional[date]:
    """직전 전량 청산일 — 재진입 판정용"""
    return _parse_date(await redis_client.get(last_exit_key(swing_id)))


async def record_full_exit(redis_client, swing_id: int, mrkt_code: str,
                           entry_price=None, exit_price=None) -> None:
    """전량 청산 후 상태 정리 — 매도 예약을 지우고 마지막 청산일을 남긴다

    전량 청산이 일어나는 모든 경로(즉시 체결·지연 체결·실보유 대조 복구)가 같이 불러야 한다.
    빠지면 예약이 남아 다음 포지션을 첫 배치에 잘못 팔고, 재진입 판정은 빠진다.
    모드가 꺼져 있어도 남긴다 (켰을 때 곧바로 판정되도록). Redis 오류는 매도 처리를 막지 않는다 —
    키가 없으면 첫 진입으로 보는 쪽이 안전한 방향이다.

    entry_price(보유 평단)·exit_price(청산 평균 체결가)를 주면 연속 손실 가드(loss_guard)에 결과를 반영한다.
    모르면 생략한다 — 연속 횟수는 그대로 남는다.
    """
    today = trading_date(mrkt_code)

    async def _write_exit():
        await clear_pending(redis_client, swing_id)
        await redis_client.setex(last_exit_key(swing_id), LAST_EXIT_TTL, today.isoformat())

    # 서로 독립으로 시도한다 — 하나가 실패해도 나머지(특히 연속 손실 기록)가 건너뛰어지지 않게
    for label, write in (("청산일", _write_exit),
                         ("연속 손실", lambda: loss_guard.record_exit(redis_client, swing_id, entry_price, exit_price, today))):
        try:
            await write()
        except Exception as e:
            logger.warning(f"[SWING_ID={swing_id}] 청산 상태({label}, Redis) 기록 실패: {e}")
