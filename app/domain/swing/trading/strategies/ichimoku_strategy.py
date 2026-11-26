"""
일목균형표 실시간 거래 전략 (SWING_TYPE='I') — 판정 규칙은 BaseIchimokuStrategy 와 백테스트가 공유한다

**신호는 전일 완성봉 기준이다.** 지표 캐시(Redis `indicators:{code}`)의 ichi_* 값은 장 시작 전 워밍업이 마지막 완성봉으로 계산하고,
이 전략은 그 값을 하루 내내 읽는다 → 전일 종가로 판정해 오늘 장 시작 뒤 첫 배치에서 주문한다 (백테스트의 "t 종가 판정, t+1 시가 체결").
진입 신호는 상태가 성립한 날부터 최대 RECENT_DAYS 일 동안 켜져 있어, 첫날 현금 부족·주문 실패가 있어도 며칠간 다시 시도한다.

단일 EMA 와 달리 쓰지 않는 것 (오케스트레이터가 읽는 전략 속성으로 끈다):
  - EMA/OBV 실시간 증분 지표 (ENRICH_REALTIME_INDICATORS=False)
  - 전량 매도 뒤 OBV 쿨다운 (COOLDOWN_AFTER_EXIT=False → 청산 직후 SIGNAL 0)
  - 종가 확인 재진입·연속 손실 가드·신용 게이트·회복 재진입 (USES_ENTRY_FILTERS=False — 단일 EMA 로 검증한 필터다)
  - 부분익절
풀 사이징의 1R 손절선은 구름 하단이다 (initial_stop / position_stop).
"""
import json
import logging
from decimal import Decimal
from typing import Dict, Optional

from app.domain.pool.entity import DEFAULT_RISK_PCT
from .base_ichimoku import BaseIchimokuStrategy
from .base_trading_strategy import TradingStrategy

logger = logging.getLogger(__name__)


class IchimokuStrategy(TradingStrategy, BaseIchimokuStrategy):
    """일목균형표 매매 전략"""

    # 사용자에게 보이는 이름 (AI 종목 분석 추천 전략 등) — 앱의 등록·설정 화면 표기와 같게 둔다
    name = "일목균형표"

    # 오케스트레이터가 읽는 전략 특성 (TradingStrategy 기본값은 단일 EMA 동작)
    ENRICH_REALTIME_INDICATORS = False
    COOLDOWN_AFTER_EXIT = False
    USES_ENTRY_FILTERS = False

    # 오케스트레이터가 읽는 인터페이스 상수 — 이 전략은 부분익절을 하지 않는다
    FIRST_PROFIT_TAKE_RATIO = 0.5
    TRADE_CYCLE_MINUTES = 5          # 설명용 (매매 배치 주기)

    # 없으면 어떤 판단도 불가한 캐시 값 — 일목 신호 + 풀 사이징·유동성에 필요한 값
    REQUIRED_CACHE_KEYS = (
        'ichi_tenkan', 'ichi_kijun', 'ichi_top', 'ichi_bot', 'ichi_state', 'ichi_entry', 'ichi_exit',
        'atr', 'close', 'high', 'low',
    )
    OPTIONAL_CACHE_DEFAULTS = {
        'open': 0.0,
        'date': None,
        'avg_daily_amount': 0.0,   # 유동성 필터 기준 (0이면 유동성 확인 불가 → 매수 차단)
    }

    @classmethod
    def describe(cls) -> dict:
        """AI 종목 분석에 전달하는 전략 설명 (수치는 판정 로직과 같은 상수)"""
        return {
            "이름": cls.name,
            "요약": (f"종가가 구름 위에 있고 전환선이 기준선 위인 상승 상태가 막 시작된 종목을 사서, "
                     f"종가가 구름 하단 아래로 마감하면 판다 (일목균형표 {cls.TENKAN_PERIOD}/{cls.KIJUN_PERIOD}/{cls.SENKOU_B_PERIOD}). "
                     "추세가 시작될 때 올라타고 구름 이탈로 끝내는 추세추종이다"),
            "매수조건": [
                "전일 종가가 구름 상단 위 (선행스팬 A·B 중 높은 쪽)",
                "전일 전환선이 기준선 위",
                f"위 두 조건의 상태가 최근 {cls.RECENT_DAYS}거래일 안에 성립해 지금도 유지 중 (오래 이어져 온 상태에는 새로 들어가지 않는다)",
                f"전일 완성봉으로 판정해 오늘 장 시작 뒤 첫 배치({cls.TRADE_CYCLE_MINUTES}분 주기)에서 주문. 현재가가 구름 하단 아래면 진입하지 않음",
            ],
            "매수차단": [
                "구름 계산에 필요한 일봉이 모자란 종목 (약 84거래일 미만)",
                "일평균 거래대금 대비 주문 금액 한도 초과 (풀 한도)",
            ],
            "매수수량": (f"계좌 총자산 기준 리스크 사이징 — 손절(구름 하단)까지 갔을 때 손실이 총자산의 약 "
                        f"{float(DEFAULT_RISK_PCT) * 100:g}%가 되는 수량 (종목당 비중 상한 있음). "
                        f"구름 하단이 멀수록 수량이 줄어든다. 진입은 사이클당 1회 (추가 매수 없음)"),
            "청산": [
                "전일 종가가 구름 하단 아래로 마감하면 오늘 장 시작 뒤 첫 배치에서 전량 매도",
                "부분익절·고정 손절선·트레일링 없음 — 구름 이탈이 유일한 청산",
            ],
            "매도후": "쿨다운 없음 — 청산 뒤 진입 조건이 다시 맞으면 바로 다시 매수 대기",
            "잘맞는종목": "구름 위에서 길게 상승 추세를 이어가는 종목, 큰 추세가 자주 나오는 대형 성장주",
            "안맞는종목": ("박스권 횡보처럼 구름 근처를 오르내리는 종목 (진입·청산이 반복되는 휩쏘 손실), 급등락이 심한 종목. "
                          "주의: 과거 검증(2020~2026, 한국·미국 100여 종목)에서 같은 낙폭의 지수 보유보다 수익이 낮았다 — 검증 미통과 전략"),
        }

    @classmethod
    async def get_cached_indicators(cls, redis_client, symbol: str) -> Optional[Dict]:
        """Redis 캐시에서 지표 조회 — 일목 값과 풀 사이징에 필요한 값 (없으면 None)"""
        try:
            cached = await redis_client.get(f"indicators:{symbol}")
            if not cached:
                return None
            data = json.loads(cached)
        except Exception as e:
            logger.warning(f"[{symbol}] 캐시 조회 실패: {e}")
            return None

        missing = [k for k in cls.REQUIRED_CACHE_KEYS if data.get(k) is None]
        if missing:
            logger.error(f"[{symbol}] 일목 캐시 필수 지표 누락 {missing} → 판단 불가. 지표 캐시 재생성(warmup) 필요")
            return None
        for key, default in cls.OPTIONAL_CACHE_DEFAULTS.items():
            data.setdefault(key, default)
            if data[key] is None:
                data[key] = default
        return data

    @classmethod
    async def check_entry_signal(
        cls, redis_client, swing_id: int, symbol: str, current_price: Decimal, frgn_ntby_qty: int, acml_vol: int,
        prdy_vrss_vol_rate: float, prdy_ctrt: float, cached_indicators: Dict,
    ) -> Optional[Dict]:
        """진입 신호 — 전일 완성봉에서 상태가 최근 RECENT_DAYS 일 안에 성립했고, 현재가가 구름 하단 위일 때"""
        if not cached_indicators or not cached_indicators.get("ichi_entry"):
            return None
        price = float(current_price)
        if price <= float(cached_indicators["ichi_bot"]):
            return None            # 시가가 이미 구름 하단 아래 — 사자마자 청산 조건이다
        return {
            "action": "BUY", "price": current_price,
            "reasons": ["일목 진입", f"구름 위 + 전환선>기준선 (최근 {cls.RECENT_DAYS}일 내 성립)"],
        }

    @classmethod
    async def check_exit_signal(
        cls, redis_client, position_id: int, symbol: str, current_price: Decimal, entry_price: Decimal,
        frgn_ntby_qty: int, acml_vol: int, cached_indicators: Dict, signal: int = 1, peak_price: float = 0,
        mrkt_code: str = "", final_batch: bool = False,
    ) -> Dict:
        """청산 신호 — 전일 종가가 구름 하단 아래로 마감했을 때 (종가 확인·마지막 배치 개념 없음)"""
        if cached_indicators and cached_indicators.get("ichi_exit"):
            return {"action": "SELL", "reasons": [
                "구름 하단 이탈",
                f"전일 종가 {float(cached_indicators['close']):,.2f} < 구름 하단 {float(cached_indicators['ichi_bot']):,.2f}",
            ]}
        return {"action": "HOLD", "reasons": []}

    @classmethod
    async def check_partial_take_profit(cls, symbol: str, current_price, entry_price, signal: int = 1) -> Optional[Dict]:
        """부분익절 없음"""
        return None

    @classmethod
    def calc_conviction(cls, adx: float, obv_z: float) -> float:
        """매수 우선순위 — 일목은 신호 강도를 구분하지 않아 모든 후보가 같다 (같으면 시장·종목코드 순)"""
        return 1.0

    @classmethod
    def initial_stop(cls, price: float, cached_indicators: Dict) -> Optional[float]:
        """풀 사이징 1R 기준 = 구름 하단 (현재가보다 낮을 때만)"""
        bot = (cached_indicators or {}).get("ichi_bot")
        return float(bot) if bot is not None and 0 < float(bot) < price else None

    @classmethod
    def position_stop(cls, cached_indicators: Dict, entry_price: float, peak_price: float) -> Optional[float]:
        """보유 포지션의 현재 손절선 = 구름 하단 (열린 리스크 계산용)"""
        bot = (cached_indicators or {}).get("ichi_bot")
        return float(bot) if bot is not None and float(bot) > 0 else None
