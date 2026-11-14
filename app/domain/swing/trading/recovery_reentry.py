"""
회복 재진입 (2026-11-14) — 청산 뒤 종목이 장기 상승 추세로 돌아오면 신호·수급 쿨다운 없이 다시 산다

배경: 큰 하락 뒤 횡보·반등에서 반복 털린 종목이 실제 추세 복귀 때 올라타지 못한다. 청산 직후 SIGNAL 3→4→0 쿨다운은
잔존 수급 때문에 바로 다시 사는 것을 막지만, 추세가 확인된 복귀에는 너무 느리다.
근거(일봉 백테스트): 총 R 이 개발·최근3년 +20%, 홀드아웃 +27%. 연속 손실 가드와 함께 쓰면 가드의 보호는 대부분 유지하면서 총 R 이 가드 단독보다 높았다.
한계: 한 사이클·생존 종목 위주의 백테스트이고, 거래가 늘어 수수료·슬리피지가 더 든다.

조건 (모두 충족해야 한다 — 전일 완성봉 기준, 마지막 청산 뒤 RECOVERY_WINDOW_DAYS 일 이내)
  ① 전일 종가 > SMA200 이고 SMA200 > 20거래일 전 SMA200  (장기 상승 추세)
  ② 전일 종가 > EMA20
  ③ 전일 종가 ≥ 마지막 청산가  (판 가격을 회복) — 그 종가가 청산 **뒤**의 봉이어야 한다(봉 날짜 > 청산일).
     청산 당일·직후에는 캐시의 전일 종가가 청산 전 값이라 갭하락 손절 뒤에도 항상 충족되어 바로 되사게 된다
  ⑤ 현재가 > 실시간 EMA20  (마지막 배치 시점에 이미 추세선 아래로 내려온 날은 사지 않는다)
  ④ 연속 손실 가드·신용 스프레드 게이트(Q3) 통과 — 호출부가 확인한다 (가드 > Q3 > 이 규칙)
매수는 마지막 배치에서만 한다(종가 확인 매매와 같은 원칙). 지표나 청산가를 모르면 사지 않는다 — 신규 진입이라 보수적으로.
"""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from app.core.config import get_settings

RECOVERY_WINDOW_DAYS = 60


@dataclass(frozen=True)
class RecoveryDecision:
    ok: bool
    reason: str


def enabled() -> bool:
    return get_settings().RECOVERY_REENTRY_ENABLED


def _num(value) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v and v > 0 else None   # NaN·0·음수는 없는 값


def _bar_date(value) -> Optional[date]:
    """캐시의 봉 날짜('20261111'·'2026-11-11' 등) → date. 읽을 수 없으면 None"""
    try:
        return datetime.strptime(str(value).replace("-", "")[:8], "%Y%m%d").date()
    except ValueError:
        return None


def decide(cached: dict, last_exit: Optional[date], last_exit_price, today: date, current_price=None) -> RecoveryDecision:
    """회복 재진입 조건 ①~③·⑤ 판정 (순수 함수) — ④(가드·Q3)와 마지막 배치 여부는 호출부 몫"""
    if last_exit is None:
        return RecoveryDecision(False, "직전 청산 기록 없음")
    days = (today - last_exit).days
    if days < 0 or days > RECOVERY_WINDOW_DAYS:
        return RecoveryDecision(False, f"직전 청산 {days}일 전 (윈도우 {RECOVERY_WINDOW_DAYS}일 밖)")

    close, ema20 = _num(cached.get("close")), _num(cached.get("ema20"))
    sma, sma_prev = _num(cached.get("sma200")), _num(cached.get("sma200_prev20"))
    exit_px = _num(last_exit_price)
    if None in (close, ema20, sma, sma_prev):
        return RecoveryDecision(False, "SMA200 등 지표 부족")
    if exit_px is None:
        return RecoveryDecision(False, "직전 청산가 모름")
    bar = _bar_date(cached.get("date"))
    if bar is None or bar <= last_exit:
        return RecoveryDecision(False, f"청산 뒤 완성봉 아직 없음 (봉 {cached.get('date')}, 청산 {last_exit})")
    price, rt_ema = _num(current_price), _num(cached.get("realtime_ema20"))
    if price is None or rt_ema is None:
        return RecoveryDecision(False, "현재가·실시간 EMA20 없음")

    if not (close > sma and sma > sma_prev):
        return RecoveryDecision(False, f"장기 추세 아님 (종가 {close:,.2f}, SMA200 {sma:,.2f}, 20일 전 {sma_prev:,.2f})")
    if not close > ema20:
        return RecoveryDecision(False, f"종가 {close:,.2f} ≤ EMA20 {ema20:,.2f}")
    if close < exit_px:
        return RecoveryDecision(False, f"종가 {close:,.2f} < 직전 청산가 {exit_px:,.2f}")
    if not price > rt_ema:
        return RecoveryDecision(False, f"현재가 {price:,.2f} ≤ 실시간 EMA20 {rt_ema:,.2f}")
    return RecoveryDecision(True, f"{days}일 전 청산({exit_px:,.2f}) 후 장기 상승 추세 복귀")
