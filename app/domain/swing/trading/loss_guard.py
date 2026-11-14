"""
연속 손실 가드 (2026-11-14) — 같은 종목에서 전량 청산이 연속 2번 손실이면 60일간 신규 진입을 막는다

배경: 큰 하락 뒤 횡보·반등 구간에서 같은 종목이 청산선 이탈 → 재진입을 반복하며 계속 털린다(SNOW 2026-10).
Q3 게이트(credit_gate)는 시장 전체 국면만 보므로 종목 단위 반복 손실은 못 막는다.
근거(일봉 백테스트): 연속 2손실 → 60일 차단이 개발·최근3년·홀드아웃 모두 거래당 평균 R 을 올렸다
(총 R 은 개발·3년 -10%, 홀드아웃 +41%). 한계: 총 수익은 줄 수 있고, 한 번의 손실은 막지 못한다 — 첫 손실 뒤
재진입 신호는 그대로 통과한다(그 거래가 이익이면 연속이 끊긴다).

손실 판정: 전량 청산 평균 체결가 < 보유 평단(ENTRY_PRICE). 부분 익절분의 이익은 반영하지 않는다
(청산선이 +20% 에서 본전 이상으로 올라가므로 부분 익절을 거친 포지션이 평단 아래로 청산되는 일은 드물다).
체결가나 평단을 모르면 연속 횟수를 바꾸지 않는다(손실도 이익도 아닌 것으로 본다).

상태는 Redis `exit_streak:{swing_id}` JSON — {"losses": 연속 손실 수, "ban_until": 차단 만료일(ISO) 또는 null}.
차단이 걸리면 연속 횟수는 0 으로 되돌려 만료 뒤 새로 센다. 키가 없거나 깨졌으면 차단 없음(fail-open).
"""
import json
import logging
from datetime import date, timedelta
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_LOSSES = 2   # 연속 손실 N 번이면 차단
BAN_DAYS = 60                # 차단 기간 (일, 달력 기준)
STATE_TTL = 120 * 86400      # 차단(60일)이 끝난 뒤에도 연속 횟수가 남도록 넉넉히


def enabled() -> bool:
    return get_settings().REENTRY_GUARD_ENABLED


def state_key(swing_id: int) -> str:
    return f"exit_streak:{swing_id}"


def valid_price(price) -> bool:
    """양수로 읽을 수 있는 가격인가"""
    try:
        return float(price) > 0
    except (TypeError, ValueError):
        return False


def is_loss(entry_price, exit_price) -> Optional[bool]:
    """손실이면 True, 아니면 False, 판정 불가(가격 누락·0 이하)면 None"""
    try:
        entry, exit_ = float(entry_price), float(exit_price)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or exit_ <= 0:
        return None
    return exit_ < entry


def next_state(losses: int, loss: Optional[bool], today: date) -> dict:
    """청산 결과를 반영한 다음 상태 (순수 함수)"""
    if loss is None:
        return {"losses": losses, "ban_until": None}
    if not loss:
        return {"losses": 0, "ban_until": None}
    losses += 1
    if losses >= MAX_CONSECUTIVE_LOSSES:
        return {"losses": 0, "ban_until": (today + timedelta(days=BAN_DAYS)).isoformat()}
    return {"losses": losses, "ban_until": None}


def _load(raw) -> dict:
    """Redis 값 → 상태. 없거나 깨졌으면 빈 상태"""
    if not raw:
        return {"losses": 0, "ban_until": None}
    try:
        data = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
        return {"losses": int(data.get("losses", 0)), "ban_until": data.get("ban_until"), "last": data.get("last")}
    except (ValueError, TypeError, AttributeError):
        return {"losses": 0, "ban_until": None}


async def record_exit(redis_client, swing_id: int, entry_price, exit_price, today: date) -> None:
    """전량 청산 결과를 상태에 반영한다 — 차단이 새로 걸리면 경고 로그

    청산이 연속 손실 판정을 하지 못하는 경로(체결가 미상)에서도 부르면 상태는 그대로 유지된다.
    이미 차단 중에 청산이 일어나면(편입 포지션 등) 기존 만료일을 유지하고 연속 횟수만 갱신한다.
    """
    previous = _load(await redis_client.get(state_key(swing_id)))
    # 같은 청산이 두 번 들어오면(Redis 기록 뒤 DB 커밋 실패 → 다음 사이클 재처리) 한 번만 센다 — 날짜·청산가가 같은 연속 기록은 중복으로 본다
    marker = f"{today.isoformat()}@{float(exit_price):.4f}" if valid_price(exit_price) else None
    if marker and previous.get("last") == marker:
        return
    state = next_state(previous["losses"], is_loss(entry_price, exit_price), today)
    state["last"] = marker or previous.get("last")
    if state["ban_until"] is None and previous["ban_until"]:
        state["ban_until"] = previous["ban_until"]
    await redis_client.setex(state_key(swing_id), STATE_TTL, json.dumps(state))
    if state["ban_until"] and state["ban_until"] != previous["ban_until"]:
        logger.warning(
            f"[SWING_ID={swing_id}] 연속 {MAX_CONSECUTIVE_LOSSES}회 손실 청산 → {state['ban_until']} 까지 신규 진입 차단"
        )


async def banned_until(redis_client, swing_id: int, today: date) -> Optional[date]:
    """차단 만료일 — 차단 중이 아니거나(만료 포함) 상태를 읽을 수 없으면 None"""
    ban = _load(await redis_client.get(state_key(swing_id)))["ban_until"]
    if not ban:
        return None
    try:
        until = date.fromisoformat(ban)
    except ValueError:
        return None
    return until if today <= until else None
