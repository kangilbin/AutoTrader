"""
스윙 매매 배치 작업 (5분 간격)
단일 20EMA 전략 + 2단계 분할 익절

SIGNAL 상태:
- 0: 대기 (포지션 없음)
- 1: 보유 (1차 익절 전)
- 2: 보유 (1차 익절 후, 잔량 보유)

배치 스케줄 (국내 기준. 미국 잡은 ET 기준으로 별도 등록):
- 08:29: ema_cache_warmup_job (EMA 캐시 워밍업)
- 08:00-15:20 5분 간격: trade_job — 단, 정규장 가드(is_market_open)를 통과한 09:00-15:30만 실제 실행
- 15:40: day_collect_job (일별 데이터 수집 — 일봉 확정 15:35 + 여유)

오케스트레이션 패턴:
- Strategy: 신호 판단만 (check_entry_signal, check_exit_signal 등)
- Entity (SwingTrade): 상태 전환 (transition_to_buy, transition_to_partial, reset_cycle 등)
- OrderExecutor: 주문 실행 (execute_buy_with_partial, execute_sell_with_partial)
- Orchestrator (이 파일): 위 세 계층을 조율
"""
import json
import logging
import asyncio
from datetime import datetime, timedelta, time as dt_time
from decimal import Decimal
from zoneinfo import ZoneInfo
from app.domain.swing.indicators import TechnicalIndicators
from app.core.config import get_settings
from app.core.market_code import is_overseas
from app.core.price import to_price
from app.external.kis_api import get_target_price, get_inquire_price
from app.external import foreign_api
from app.common.database import Database
from app.domain.swing.service import SwingService
from app.domain.stock.service import StockService
from app.domain.stock.stock_data_batch import is_today_incomplete
from app.domain.trade_history import TradeHistoryService
from .order_executor import SwingOrderExecutor
from .trading_strategy_factory import TradingStrategyFactory
from .strategies.base_single_ema import BaseSingleEMAStrategy
from app.common.redis import Redis
from app.domain.notification.service import PushNotificationService

logger = logging.getLogger(__name__)

# ===== 시장별 정규장 시간 (로컬 타임존 기준) =====
_US_OPEN = {"open": dt_time(9, 30), "close": dt_time(16, 0), "tz": "America/New_York"}
_KR_OPEN = {"open": dt_time(9, 0), "close": dt_time(15, 30), "tz": "Asia/Seoul"}
_MARKET_OPEN_CONFIG = {
    "J":   _KR_OPEN,
    "NX":  _KR_OPEN,
    "UN":  _KR_OPEN,
    "NYS": _US_OPEN,
    "NAS": _US_OPEN,
    "AMS": _US_OPEN,
}


def is_market_open(mrkt_code: str, now: datetime = None) -> bool:
    """정규장 시간 여부 (주말 제외)

    스케줄 범위가 넓어도 프리마켓/장 마감 후에 주문이 나가지 않도록 배치 진입점에서 사용한다.
    프리마켓 시세는 거래량이 사실상 0이라 실시간 지표(OBV/accum/ATR)가 왜곡되고,
    그 상태로 낸 주문은 개장까지 대기하다 의도치 않은 가격에 체결된다.

    ⚠️ 공휴일(휴장일)은 판별하지 않는다 — 휴장일에는 시세가 전일 종가로 고정되므로
       신호가 발생할 수 있고 주문은 다음 거래일로 넘어간다. 휴장일 캘린더가 필요하면 별도 도입.
    """
    config = _MARKET_OPEN_CONFIG.get(mrkt_code, _KR_OPEN)
    tz = ZoneInfo(config["tz"])
    now = now.astimezone(tz) if now else datetime.now(tz)

    if now.weekday() >= 5:  # 토·일
        return False

    return config["open"] <= now.time() < config["close"]


def is_opening_guard(mrkt_code: str, guard_minutes: int = 10) -> bool:
    """
    개장 초기 보호 기간인지 판단

    개장 직후 호가 불균형/시초가 오버슈팅으로 인한
    장중고가 스파이크가 PEAK_PRICE를 오염시키는 것을 방지합니다.
    """
    config = _MARKET_OPEN_CONFIG.get(mrkt_code, _MARKET_OPEN_CONFIG["J"])
    tz = ZoneInfo(config["tz"])
    now = datetime.now(tz).time()

    open_dt = datetime.combine(datetime.today(), config["open"])
    guard_end = (open_dt + timedelta(minutes=guard_minutes)).time()

    return config["open"] <= now < guard_end


def _trading_allowed(mrkt_code: str, label: str) -> bool:
    """매매 배치 실행 가능 여부 (정규장 가드)

    cron 범위가 넓게 잡혀 있어도 장 시간 밖에는 실행하지 않는다.
    DB 세션 획득 전에 판단해 휴장 시간대의 불필요한 커넥션/쿼리도 막는다.
    """
    if is_market_open(mrkt_code):
        return True

    if get_settings().ALLOW_OFFHOURS_TRADING:
        logger.warning(f"[{label}] 정규장 시간이 아니지만 ALLOW_OFFHOURS_TRADING=true → 실행")
        return True

    logger.debug(f"[{label}] 정규장 시간 아님 → 스킵")
    return False


# ===== 동시 실행 제어 =====
_SEMAPHORE = asyncio.Semaphore(5)  # 동시에 최대 5개 종목 처리


async def trade_job():
    """국내 매매 신호 확인 및 실행 (5분 단위) — 국내 종목만"""
    if not _trading_allowed("J", "BATCH"):
        return

    db = await Database.get_session()
    try:
        swing_service = SwingService(db)
        redis_client = await Redis.get_connection()
        swing_list = await swing_service.get_active_domestic_swings()
        logger.info(f"[BATCH START] 활성 국내 스윙 수: {len(swing_list)}")
    except Exception as e:
        logger.error(f"trade_job 스윙 목록 조회 실패: {e}", exc_info=True)
        return
    finally:
        await db.close()

    tasks = [
        process_single_swing(swing_row, redis_client)
        for swing_row in swing_list
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    success_count = sum(1 for r in results if not isinstance(r, Exception))
    error_count = len(results) - success_count
    logger.info(
        f"[BATCH END] 배치 작업 완료 - "
        f"성공: {success_count}, 실패: {error_count}, 총: {len(results)}"
    )


async def process_single_swing(
    swing_row,
    redis_client
):
    """
    개별 스윙 매매 오케스트레이터 (세마포어로 동시 실행 제어)

    Strategy(신호 판단) + Entity(상태 전환) + OrderExecutor(주문 실행) 조율

    Args:
        swing_row: SWING_TRADE 조인 결과 (USER_ID, API_KEY, SECRET_KEY 포함)
        redis_client: Redis 클라이언트
    """
    async with _SEMAPHORE:
        db = await Database.get_session()
        try:
            swing_service = SwingService(db)
            swing_id = swing_row.SWING_ID
            st_code = swing_row.ST_CODE
            user_id = swing_row.USER_ID if hasattr(swing_row, 'USER_ID') else None
            swing_type = swing_row.SWING_TYPE if hasattr(swing_row, 'SWING_TYPE') else 'S'

            if not user_id:
                logger.warning(f"[SWING_ID={swing_id}] USER_ID가 없습니다. 계좌-인증키 연결을 확인하세요.")
                return


            swing = await swing_service.repo.find_by_id(swing_id)
            if not swing:
                logger.warning(f"[{swing_id}] 스윙 엔티티 로드 실패")
                return

            # 전략 선택
            strategy = TradingStrategyFactory.get_strategy(swing_type)

            # === 1. 데이터 수집 ===
            mrkt_code = swing.MRKT_CODE
            _overseas = is_overseas(mrkt_code)

            cached_indicators = await strategy.get_cached_indicators(redis_client, st_code)
            if not cached_indicators:
                # SIGNAL=0이어도 HOLD_QTY>0이면 실제 포지션이 남아있다 (편입 대기 상태).
                # has_position()만 보면 이 경우를 warning으로 흘려보내 무방비 상태를 놓친다.
                if swing.has_position() or (swing.HOLD_QTY or 0) > 0:
                    # 지표가 없으면 손절/익절 판단 자체가 불가 — 포지션이 무방비로 남는다
                    logger.error(
                        f"[{st_code}] 지표 캐시 없음 + 포지션 보유(SIGNAL={swing.SIGNAL}, "
                        f"{swing.HOLD_QTY}주) → 손절/익절 평가 불가, 캐시 재생성 필요"
                    )
                else:
                    logger.warning(f"[{st_code}] 등록된 캐시 정보가 없습니다.")
                return

            if _overseas:
                current_price_data = await foreign_api.get_inquire_price(user_id, st_code, swing_service.db, excd=mrkt_code)
            else:
                response = await get_inquire_price(user_id, st_code, swing_service.db)
                current_price_data = response.get("output", {}) if isinstance(response, dict) else response
            if not current_price_data:
                logger.warning(f"[{st_code}] 현재가 조회 실패")
                return

            if _overseas:
                current_price = Decimal(str(current_price_data.get("last", 0)))
                current_high = Decimal(str(current_price_data.get("high", current_price)))
                current_low = Decimal(str(current_price_data.get("low", current_price)))
                acml_vol = int(current_price_data.get("tvol", 0))
                frgn_ntby_qty = 0  # 해외 시 외국인 순매수 미제공
                prdy_vrss_vol_rate = 100.0  # 해외 시 미제공, 기본값
                # 현재가상세(HHDFS76200200)엔 현지통화 등락률 필드가 없어
                # 국장 prdy_ctrt(전일 대비율)와 동일하게 (last-base)/base 로 계산
                base_price = float(current_price_data.get("base", 0))
                prdy_ctrt = round((float(current_price) - base_price) / base_price * 100, 2) if base_price else 0.0
            else:
                current_price = Decimal(str(current_price_data.get("stck_prpr", 0)))
                current_high = Decimal(str(current_price_data.get("stck_hgpr", current_price)))
                current_low = Decimal(str(current_price_data.get("stck_lwpr", current_price)))
                acml_vol = int(current_price_data.get("acml_vol", 0))
                frgn_ntby_qty = int(current_price_data.get("frgn_ntby_qty", 0))
                prdy_vrss_vol_rate = float(current_price_data.get("prdy_vrss_vol_rate", 100))
                prdy_ctrt = float(current_price_data.get("prdy_ctrt", 0))

            # 실시간 지표 증분 계산 (base 전략 상수와 동기화)
            cached_indicators = TechnicalIndicators.enrich_cached_indicators_with_realtime(
                cached_indicators=cached_indicators,
                current_price=float(current_price),
                current_volume=acml_vol,
                current_high=float(current_high),
                current_low=float(current_low),
                ema_period=BaseSingleEMAStrategy.EMA_PERIOD,
                atr_period=14,
                obv_lookback=BaseSingleEMAStrategy.OBV_LOOKBACK,
                accum_ema_period=BaseSingleEMAStrategy.ACCUM_EMA_PERIOD
            )

            avg_daily_amount = cached_indicators["avg_daily_amount"]

            # === 2. 미확인 주문 재확인 / 부분 체결 진행 중 체크 (신호 로직보다 우선) ===
            partial_key = f"partial_exec:{swing_id}"
            pending_key = f"pending_order:{swing_id}"
            partial_state_str = await redis_client.get(partial_key)
            pending_str = await redis_client.get(pending_key)

            if (pending_str or partial_state_str) and user_id:
                entry_price = float(swing.ENTRY_PRICE) if swing.ENTRY_PRICE else 0
                hold_qty = swing.HOLD_QTY or 0
                prev_signal = swing.SIGNAL

                if pending_str:
                    # 체결 미확인 주문이 있으면 재확인이 최우선 — 확인 전까지 신규 주문 금지 (중복 주문 방지)
                    partial_result = await SwingOrderExecutor.resolve_pending_order(
                        pending=json.loads(pending_str),
                        partial_state=json.loads(partial_state_str) if partial_state_str else None,
                        swing_id=swing_id,
                        user_id=user_id,
                        st_code=st_code,
                        current_price=current_price,
                        current_entry_price=entry_price,
                        current_hold_qty=hold_qty,
                        db=db,
                    )
                else:
                    partial_result = await SwingOrderExecutor.continue_partial_execution(
                        redis_client=redis_client,
                        swing_id=swing_id,
                        user_id=user_id,
                        st_code=st_code,
                        current_price=current_price,
                        avg_daily_amount=avg_daily_amount,
                        cached_indicators=cached_indicators,
                        current_entry_price=entry_price,
                        current_hold_qty=hold_qty,
                        db=db,
                        mrkt_code=mrkt_code,
                    )

                # 포지션 보유 중 미확인 주문이 남으면 손절/익절 평가가 최대
                # MAX_PENDING_ATTEMPTS 사이클 동안 멈춘다. 주문을 방치하는 것보다
                # 포지션이 무방비인 게 위험하므로, 주문을 정리하고 이번 사이클에
                # 정상 평가로 복귀한다. (취소 실패 = 체결됐을 수 있음 → 기존대로 재확인 대기)
                resume_normal_flow = False
                if pending_str and partial_result.get("pending_state") and swing.has_position():
                    _pending = json.loads(pending_str)
                    if await SwingOrderExecutor.cancel_order(
                        user_id, _pending.get("order_no"), st_code,
                        _pending.get("mrkt_code", mrkt_code), db,
                        _pending.get("ord_orgno", ""),
                    ):
                        partial_result.pop("pending_state")  # 아래 Redis 정리에서 키 삭제
                        resume_normal_flow = True
                        logger.warning(
                            f"[{st_code}] 포지션 보유 중 미확인 주문 취소 → 손절/익절 평가 재개"
                        )

                if partial_result.get("completed") or partial_result.get("aborted"):
                    new_signal = partial_result.get("signal_on_complete", swing.SIGNAL)
                    if new_signal == 0:
                        obv_z = cached_indicators.get('realtime_obv_z', 0) if cached_indicators else 0
                        swing.reset_cycle(obv_z=obv_z)
                    elif new_signal == 2 and swing.SIGNAL == 1:
                        # 1차 익절 분할 체결 완료
                        sold_qty = partial_result.get("qty", 0)
                        swing.transition_to_partial(sold_qty)
                        swing.PEAK_PRICE = to_price(current_price)
                    else:
                        swing.SIGNAL = new_signal
                        swing.MOD_DT = datetime.now()

                # CUR_AMOUNT 업데이트 (분할 체결 chunk 금액 반영)
                chunk_amount = partial_result.get("chunk_amount", 0)
                if chunk_amount > 0:
                    exec_type = partial_result.get("exec_type")
                    if exec_type == "buy":
                        swing.deduct_amount(chunk_amount)
                    elif exec_type == "sell":
                        swing.add_amount(chunk_amount)

                # Entity 상태 업데이트
                if partial_result.get("entry_price"):
                    swing.ENTRY_PRICE = to_price(partial_result["entry_price"])
                if partial_result.get("hold_qty") is not None:
                    swing.HOLD_QTY = partial_result["hold_qty"]

                # 지연 체결/매수 중단으로 포지션만 남은 경우 PEAK 초기화 (익절 추적 기준 확보)
                # 급락 중이면 현재가가 평단가보다 낮으므로 평단가를 하한으로 둔다
                # (PEAK가 평단 아래로 잡히면 트레일링 익절 기준이 비정상적으로 낮아짐)
                if swing.has_position() and not swing.PEAK_PRICE:
                    entry_floor = float(swing.ENTRY_PRICE) if swing.ENTRY_PRICE else 0
                    swing.PEAK_PRICE = to_price(max(float(current_price), entry_floor))

                await db.flush()
                await db.commit()

                # DB commit 성공 후 Redis 정리/갱신
                if partial_result.get("completed") or partial_result.get("aborted"):
                    await redis_client.delete(partial_key)
                elif partial_result.get("partial_state"):
                    # 진행 중: 갱신된 partial state를 Redis에 저장
                    await redis_client.setex(
                        partial_key, 86400,
                        json.dumps(partial_result["partial_state"])
                    )

                if pending_str:
                    if partial_result.get("pending_state"):
                        # 아직 체결 미확인 → 재확인 횟수 갱신
                        await redis_client.setex(
                            pending_key, 86400,
                            json.dumps(partial_result["pending_state"])
                        )
                    else:
                        # 확인 완료(또는 추적 종료) → 다음 사이클부터 정상 신호 로직
                        await redis_client.delete(pending_key)
                elif partial_result.get("pending_order"):
                    # 이번 사이클 chunk 주문의 체결을 확인 못함 → 다음 사이클에 재확인
                    await redis_client.setex(
                        pending_key, 86400,
                        json.dumps(partial_result["pending_order"])
                    )

                # 푸쉬 알림
                if user_id and swing.SIGNAL != prev_signal:
                    _fire_trade_notification(
                        user_id, swing, prev_signal, st_code,
                        prev_hold_qty=hold_qty, current_price=float(current_price)
                    )

                # 미확인 주문을 취소한 경우에만 이어서 손절/익절 평가를 수행한다
                # (SIGNAL 1/2 상태이므로 신규 진입 로직은 타지 않는다)
                if not resume_normal_flow:
                    return

            # === 2-1. 불변식 강제: SIGNAL 0(매수대기) + HOLD_QTY>0 은 성립할 수 없다 ===
            # 신규 진입으로 처리하면 transition_to_buy가 기존 수량/평단을 덮어써 포지션이 유실된다.
            # 발생원: 계좌 보유종목 자동등록분(mapping_swing) / 분할매수 중 partial_exec 키 소실
            if swing.is_waiting() and (swing.HOLD_QTY or 0) > 0:
                _entry_price = float(swing.ENTRY_PRICE) if swing.ENTRY_PRICE else 0
                if _entry_price <= 0:
                    logger.error(
                        f"[{st_code}] SIGNAL=0 + {swing.HOLD_QTY}주 보유 + 평단 없음 "
                        f"→ 편입 불가, 이번 사이클 스킵 (수동 확인 필요)"
                    )
                    return
                swing.adopt_position(
                    hold_qty=swing.HOLD_QTY,
                    entry_price=_entry_price,
                    current_price=float(current_price),
                )
                logger.warning(
                    f"[{st_code}] 고아 포지션 편입: {swing.HOLD_QTY}주 @ {_entry_price:,.2f} "
                    f"→ SIGNAL=1 (신규 매수 차단, 손절/익절 평가로 전환)"
                )
                # prev_signal 캡처보다 앞에 둔다 — 편입은 체결이 아니므로
                # 0→1 전환을 매수 완료 푸시로 오인해서는 안 된다 (_fire_trade_notification)

            # === 3. PEAK_PRICE 갱신 (현재가 기준, 노이즈 방지) ===
            if swing.has_position():
                swing.update_peak_price(float(current_price))

            # 변경 전 SIGNAL/수량 저장 (알림용)
            # 전량 매도 시 reset_cycle이 HOLD_QTY를 0으로 지우므로 미리 잡아둔다
            prev_signal = swing.SIGNAL
            prev_hold_qty = swing.HOLD_QTY or 0

            # === 4. SIGNAL별 오케스트레이션 ===
            if swing.is_waiting():
                await _handle_waiting(
                    swing, strategy, redis_client, db, user_id, st_code,
                    current_price, frgn_ntby_qty, acml_vol,
                    prdy_vrss_vol_rate, prdy_ctrt,
                    cached_indicators, avg_daily_amount, mrkt_code
                )

            elif swing.has_position():
                await _handle_position(
                    swing, strategy, redis_client, db, user_id, st_code,
                    current_price, frgn_ntby_qty, acml_vol,
                    cached_indicators, avg_daily_amount, mrkt_code
                )

            elif swing.is_cooling_down():
                _handle_cooling_down(swing, strategy, st_code, cached_indicators)

            # === 5. 변경사항 저장 ===
            await db.flush()
            await db.commit()

            # === 5-1. 부분 체결 Redis 상태 저장 (DB commit 성공 후) ===
            pending_partial = getattr(swing, '_pending_partial_state', None)
            if pending_partial:
                await redis_client.setex(
                    f"partial_exec:{swing.SWING_ID}", 86400,
                    json.dumps(pending_partial)
                )
                swing._pending_partial_state = None

            # 체결 미확인 주문 → 다음 사이클 재확인 대상으로 등록
            pending_order = getattr(swing, '_pending_order_state', None)
            if pending_order:
                await redis_client.setex(
                    f"pending_order:{swing.SWING_ID}", 86400,
                    json.dumps(pending_order)
                )
                swing._pending_order_state = None

            # === 6. 푸쉬 알림 ===
            if user_id and swing.SIGNAL != prev_signal:
                _fire_trade_notification(
                    user_id, swing, prev_signal, st_code,
                    prev_hold_qty=prev_hold_qty, current_price=float(current_price)
                )

        except Exception as e:
            await db.rollback()
            logger.error(
                f"스윙 처리 실패 (SWING_ID={swing_row.SWING_ID}, ST_CODE={swing_row.ST_CODE}): {e}",
                exc_info=True
            )
        finally:
            await db.close()


# ==================== SIGNAL 핸들러 ====================


def _handle_cooling_down(swing, strategy, st_code: str, cached_indicators: dict):
    """
    수급 안정화 대기 (2단계)

    SIGNAL 3 (수급 이탈 대기): OBV z < COOLDOWN_OBV_EXIT → SIGNAL 4
    SIGNAL 4 (수급 재유입 대기): OBV z > COOLDOWN_OBV_REENTRY → SIGNAL 0
    """
    obv_z = cached_indicators.get('realtime_obv_z', 0)

    if swing.SIGNAL == 3:
        if obv_z < strategy.COOLDOWN_OBV_EXIT:
            swing.transition_to_reentry_waiting()
            logger.info(f"[{st_code}] 수급 이탈 확인 (OBV z={obv_z:.2f} < {strategy.COOLDOWN_OBV_EXIT}) → 재유입 대기(SIGNAL 4)")
        else:
            logger.debug(f"[{st_code}] 수급 이탈 대기 중 (OBV z={obv_z:.2f})")

    elif swing.SIGNAL == 4:
        if obv_z > strategy.COOLDOWN_OBV_REENTRY:
            swing.transition_to_waiting()
            logger.info(f"[{st_code}] 수급 재유입 확인 (OBV z={obv_z:.2f} > {strategy.COOLDOWN_OBV_REENTRY}) → 매수 대기(SIGNAL 0)")
        else:
            logger.debug(f"[{st_code}] 수급 재유입 대기 중 (OBV z={obv_z:.2f})")


async def _handle_waiting(
    swing, strategy, redis_client, db, user_id, st_code,
    current_price, frgn_ntby_qty, acml_vol,
    prdy_vrss_vol_rate, prdy_ctrt,
    cached_indicators, avg_daily_amount, mrkt_code=""
):
    """대기 상태 → 매수 신호 확인 (리스크 기반 포지션 사이징)"""
    entry_result = await strategy.check_entry_signal(
        redis_client=redis_client,
        swing_id=swing.SWING_ID,
        symbol=st_code,
        current_price=current_price,
        frgn_ntby_qty=frgn_ntby_qty,
        acml_vol=acml_vol,
        prdy_vrss_vol_rate=prdy_vrss_vol_rate,
        prdy_ctrt=prdy_ctrt,
        cached_indicators=cached_indicators
    )

    if not (entry_result and entry_result.get("action") == "BUY"):
        return

    if not user_id:
        logger.warning(f"[{st_code}] USER_ID 없음, 주문 실행 불가")
        return

    # Conviction 기반 포지션 사이징
    realtime_adx = cached_indicators.get('realtime_adx', 0)
    realtime_obv_z = cached_indicators.get('realtime_obv_z', 0)
    equity = float(swing.CUR_AMOUNT)
    curr_price = float(current_price)

    conviction = strategy.calc_conviction(realtime_adx, realtime_obv_z)
    target_qty = int(equity * strategy.MAX_ENTRY_PCT * conviction / curr_price) if curr_price > 0 else 0

    logger.info(f"[{st_code}] 포지션 사이징: conviction={conviction:.2f}, "
                f"투입비={strategy.MAX_ENTRY_PCT * conviction:.1%}, 수량={target_qty}")

    target_amount = Decimal(str(target_qty)) * current_price if target_qty > 0 else Decimal(0)

    if target_qty <= 0:
        logger.info(f"[{st_code}] 매수 수량 부족 (CUR_AMOUNT={equity:,.0f}원)")
        return

    # 체결 이력 저장은 executor가 전담한다 (체결 수량/단가를 아는 지점) — 사유만 넘긴다
    order_result = await SwingOrderExecutor.execute_buy_with_partial(
        swing_id=swing.SWING_ID,
        user_id=user_id,
        st_code=st_code,
        current_price=current_price,
        target_amount=target_amount,
        avg_daily_amount=avg_daily_amount,
        signal_on_complete=1,
        db=db,
        mrkt_code=mrkt_code,
        reasons=entry_result.get("reasons", ["매수"]).copy(),
    )

    if not order_result.get("success"):
        logger.error(f"[{st_code}] 매수 실패: {order_result.get('reason')}")
        return

    # 주문은 나갔으나 체결 확인 실패 → 상태/이력 반영 없이 다음 사이클에 재확인
    # (0주·0원 이력 저장과 SIGNAL 미변경으로 인한 중복 주문 방지)
    if order_result.get("unconfirmed"):
        swing._pending_order_state = order_result.get("pending_order")
        logger.warning(f"[{st_code}] 매수 주문 체결 미확인 → 다음 사이클 재확인 대기")
        return

    if order_result.get("partial_state"):
        swing._pending_partial_state = order_result["partial_state"]

    avg_price = order_result.get("avg_price", float(current_price))
    qty = order_result.get("qty", 0)

    # CUR_AMOUNT 차감 (매수 금액만큼 가용 금액 감소)
    if qty > 0:
        swing.deduct_amount(avg_price * qty)

    if order_result.get("completed", True):
        swing.transition_to_buy(avg_price, qty, float(current_price))
    else:
        swing.ENTRY_PRICE = to_price(avg_price)
        swing.HOLD_QTY = qty
        swing.MOD_DT = datetime.now()



async def _handle_position(
    swing, strategy, redis_client, db, user_id, st_code,
    current_price, frgn_ntby_qty, acml_vol,
    cached_indicators, avg_daily_amount, mrkt_code=""
):
    """보유 상태 → 단일 청산선 판정 + (선택) 부분 익절

    청산선 하나가 손실 제한 → 본전 확보 → 이익 확정을 순서대로 수행하므로
    손절/1차 익절/2차 익절을 따로 판정하지 않는다.
    """
    entry_price = float(swing.ENTRY_PRICE) if swing.ENTRY_PRICE else 0
    hold_qty = swing.HOLD_QTY or 0
    peak_price = float(swing.PEAK_PRICE) if swing.PEAK_PRICE else 0

    if entry_price <= 0 or hold_qty <= 0:
        return

    # 1. 청산선 이탈 확인 (최우선)
    exit_result = await strategy.check_exit_signal(
        redis_client=redis_client,
        position_id=swing.SWING_ID,
        symbol=st_code,
        current_price=current_price,
        entry_price=Decimal(str(entry_price)),
        frgn_ntby_qty=frgn_ntby_qty,
        acml_vol=acml_vol,
        cached_indicators=cached_indicators,
        signal=swing.SIGNAL,
        peak_price=peak_price,
    )

    if exit_result and exit_result.get("action") == "SELL":
        await _execute_full_sell(
            swing, redis_client, db, user_id, st_code,
            current_price, hold_qty, avg_daily_amount,
            exit_result.get("reasons", ["청산"]),
            f"[{user_id} - 주식: {st_code}] 청산선 이탈 전량 매도, 사이클 종료",
            mrkt_code=mrkt_code,
            cached_indicators=cached_indicators
        )
        return

    # 2. 부분 익절 (목표 수익률 도달 시 절반, SIGNAL 1에서 1회)
    tp_result = await strategy.check_partial_take_profit(
        symbol=st_code,
        current_price=current_price,
        entry_price=Decimal(str(entry_price)),
        signal=swing.SIGNAL,
    )

    if tp_result and tp_result.get("action") == "SELL_HALF":
        sell_qty = int(hold_qty * strategy.FIRST_PROFIT_TAKE_RATIO)

        # 1주뿐이면 절반 분할이 불가능하다. 그대로 두면 매 사이클 신호만 반복되므로
        # 전량 매도로 사이클을 종료한다.
        if sell_qty <= 0:
            await _execute_full_sell(
                swing, redis_client, db, user_id, st_code,
                current_price, hold_qty, avg_daily_amount,
                tp_result.get("reasons", ["부분익절"]) + ["잔량 1주 전량 매도"],
                f"[{user_id} - 주식: {st_code}] 잔량 1주 익절 전량 매도, 사이클 종료",
                mrkt_code=mrkt_code,
                cached_indicators=cached_indicators
            )
            return

        await _execute_partial_sell(
            swing, redis_client, db, user_id, st_code,
            current_price, sell_qty, avg_daily_amount,
            tp_result.get("reasons", ["부분익절"]),
            f"[{user_id} - 주식: {st_code}] 부분 익절 {sell_qty}주 매도 완료",
            mrkt_code=mrkt_code
        )


# ==================== 공통 매도 헬퍼 ====================


async def _execute_partial_sell(
    swing, redis_client, db, user_id, st_code,
    current_price, sell_qty, avg_daily_amount,
    reasons, success_log_msg, mrkt_code=""
):
    """1차 익절 50% 매도 실행 → Entity transition_to_partial()"""
    if not user_id:
        logger.warning(f"[{st_code}] USER_ID 없음, 매도 주문 실행 불가")
        return

    order_result = await SwingOrderExecutor.execute_sell_with_partial(
        swing_id=swing.SWING_ID,
        user_id=user_id,
        st_code=st_code,
        current_price=current_price,
        target_qty=sell_qty,
        avg_daily_amount=avg_daily_amount,
        signal_on_complete=2,
        db=db,
        mrkt_code=mrkt_code,
        reasons=list(reasons),
    )

    if not order_result.get("success"):
        logger.error(f"[{st_code}] 1차 익절 매도 실패: {order_result.get('reason')}")
        return

    if order_result.get("unconfirmed"):
        swing._pending_order_state = order_result.get("pending_order")
        logger.warning(f"[{st_code}] 1차 익절 매도 체결 미확인 → 다음 사이클 재확인 대기")
        return

    if order_result.get("partial_state"):
        swing._pending_partial_state = order_result["partial_state"]

    # CUR_AMOUNT 가산 (매도 금액만큼 가용 금액 증가)
    sold_qty_for_amount = order_result.get("qty", 0)
    if sold_qty_for_amount > 0:
        sell_price = order_result.get("avg_price", float(current_price))
        swing.add_amount(sell_price * sold_qty_for_amount)

    if order_result.get("completed", True):
        actual_sold = order_result.get("qty", sell_qty)
        swing.transition_to_partial(actual_sold)
        # PEAK를 현재가로 리셋 → 2차 익절 새로 추적
        swing.PEAK_PRICE = to_price(current_price)
    else:
        sold_qty = order_result.get("qty", 0)
        swing.update_hold_qty_partial(sold_qty)

    logger.info(success_log_msg)


async def _execute_full_sell(
    swing, redis_client, db, user_id, st_code,
    current_price, hold_qty, avg_daily_amount,
    reasons, success_log_msg, mrkt_code="",
    cached_indicators=None
):
    """전량 매도 실행 → Entity reset_cycle(obv_z)"""
    if not user_id:
        logger.warning(f"[{st_code}] USER_ID 없음, 매도 주문 실행 불가")
        return

    order_result = await SwingOrderExecutor.execute_sell_with_partial(
        swing_id=swing.SWING_ID,
        user_id=user_id,
        st_code=st_code,
        current_price=current_price,
        target_qty=hold_qty,
        avg_daily_amount=avg_daily_amount,
        signal_on_complete=0,
        db=db,
        mrkt_code=mrkt_code,
        reasons=list(reasons),
    )

    if not order_result.get("success"):
        logger.error(f"[{st_code}] 매도 실패: {order_result.get('reason')}")
        return

    if order_result.get("unconfirmed"):
        swing._pending_order_state = order_result.get("pending_order")
        logger.warning(f"[{st_code}] 전량 매도 체결 미확인 → 다음 사이클 재확인 대기")
        return

    # order_result에 partial_state가 있으면 Entity에 임시 저장
    if order_result.get("partial_state"):
        swing._pending_partial_state = order_result["partial_state"]

    # CUR_AMOUNT 가산 (매도 금액만큼 가용 금액 증가)
    sold_qty_for_amount = order_result.get("qty", 0)
    if sold_qty_for_amount > 0:
        sell_price = order_result.get("avg_price", float(current_price))
        swing.add_amount(sell_price * sold_qty_for_amount)

    if order_result.get("completed", True):
        obv_z = cached_indicators.get('realtime_obv_z', 0) if cached_indicators else 0
        swing.reset_cycle(obv_z=obv_z)
    else:
        sold_qty = order_result.get("qty", 0)
        swing.update_hold_qty_partial(sold_qty)

    logger.info(success_log_msg)


# ==================== 기타 배치 작업 ====================


async def day_collect_job():
    """
    국내 일별 데이터 수집 (일봉 확정 15:35 + 여유 → 15:40 KST 실행)

    작업: 국내 활성 스윙의 당일 OHLCV 데이터 수집
    병렬 처리: 최대 5개 종목 동시 실행
    """
    if is_today_incomplete("J"):
        # 여기 걸리면 그날 OHLCV가 통째로 비고 재시도도 없다 (다음 워밍업 지표까지 오염)
        logger.error("[DAY COLLECT KR] 세션 미완료 상태에서 수집 잡 실행 → 전 종목 스킵. 스케줄 확인 필요")
        return

    logger.info("[DAY COLLECT KR] 국내 데이터 수집 시작")
    db = await Database.get_session()

    try:
        stock_service = StockService(db)

        data_target_stocks = await stock_service.get_data_target_stocks(overseas=False)
        logger.info(f"[DAY COLLECT KR] 데이터 수집 대상 종목 수: {len(data_target_stocks)}")

        # 병렬 처리: asyncio.gather로 모든 종목 동시 실행
        tasks = [
            collect_single_stock(stock, stock_service)
            for stock in data_target_stocks
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 결과 로깅
        success_count = sum(1 for r in results if not isinstance(r, Exception))
        error_count = len(results) - success_count

        logger.info(
            f"[DAY COLLECT KR] 데이터 수집 완료 - "
            f"성공: {success_count}, 실패: {error_count}, 총: {len(results)}"
        )

    except Exception as e:
        logger.error(f"[DAY COLLECT KR] day_collect_job 실패: {e}", exc_info=True)
    finally:
        await db.close()


async def us_day_collect_job():
    """
    미국 일별 데이터 수집 (일봉 확정 16:35 ET + 여유 → 16:40 ET 실행)

    작업: 해외 활성 스윙의 당일 OHLCV 데이터 수집
    병렬 처리: 최대 5개 종목 동시 실행
    """
    if is_today_incomplete("NAS"):
        logger.error("[DAY COLLECT US] 세션 미완료 상태에서 수집 잡 실행 → 전 종목 스킵. 스케줄 확인 필요")
        return

    logger.info("[DAY COLLECT US] 미국 데이터 수집 시작")
    db = await Database.get_session()

    try:
        stock_service = StockService(db)

        data_target_stocks = await stock_service.get_data_target_stocks(overseas=True)
        logger.info(f"[DAY COLLECT US] 데이터 수집 대상 종목 수: {len(data_target_stocks)}")

        tasks = [
            collect_single_stock(stock, stock_service)
            for stock in data_target_stocks
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        success_count = sum(1 for r in results if not isinstance(r, Exception))
        error_count = len(results) - success_count

        logger.info(
            f"[DAY COLLECT US] 데이터 수집 완료 - "
            f"성공: {success_count}, 실패: {error_count}, 총: {len(results)}"
        )

    except Exception as e:
        logger.error(f"[DAY COLLECT US] us_day_collect_job 실패: {e}", exc_info=True)
    finally:
        await db.close()


async def collect_single_stock(stock, stock_service: StockService):
    """
    개별 종목 데이터 수집 (세마포어로 동시 실행 제어)

    Args:
        stock: STOCK_INFO 레코드
        stock_service: StockService 인스턴스
    """
    async with _SEMAPHORE:
        code = stock.ST_CODE
        mrkt_code = stock.MRKT_CODE
        _overseas = is_overseas(mrkt_code)

        # 세션 미완료(프리마켓/장중/주말)면 당일 부분봉 저장 방지 — 완성봉만 적재
        if is_today_incomplete(mrkt_code):
            logger.info(f"[DAY COLLECT] {code} 세션 미완료 — 당일 저장 스킵")
            return

        try:
            if _overseas:
                excd = mrkt_code
                response = await foreign_api.get_target_price(code, excd)
            else:
                response = await get_target_price(code)

            if response:
                if _overseas:
                    history_data = [{
                        "MRKT_CODE": mrkt_code,
                        "ST_CODE": code,
                        # KIS 응답의 실제 거래일(xymd, ET 기준) 사용 — 서버 시계 무관. 누락 시 ET 오늘로 폴백
                        "STCK_BSOP_DATE": response.get('xymd') or datetime.now(ZoneInfo("America/New_York")).strftime('%Y%m%d'),
                        "STCK_OPRC": response.get('open'),
                        "STCK_HGPR": response.get('high'),
                        "STCK_LWPR": response.get('low'),
                        "STCK_CLPR": response.get('clos'),
                        "ACML_VOL": response.get('tvol'),
                        "FRGN_NTBY_QTY": 0,
                        "REG_DT": datetime.now()
                    }]
                else:
                    history_data = [{
                        "MRKT_CODE": mrkt_code,
                        "ST_CODE": code,
                        # KIS 응답의 실제 거래일(stck_bsop_date) 사용. 누락 시 KST 오늘로 폴백
                        "STCK_BSOP_DATE": response.get('stck_bsop_date') or datetime.now(ZoneInfo("Asia/Seoul")).strftime('%Y%m%d'),
                        "STCK_OPRC": response.get('stck_oprc'),
                        "STCK_HGPR": response.get('stck_hgpr'),
                        "STCK_LWPR": response.get('stck_lwpr'),
                        "STCK_CLPR": response.get('stck_clpr'),
                        "ACML_VOL": response.get('acml_vol'),
                        "FRGN_NTBY_QTY": response.get('frgn_ntby_qty'),
                        "REG_DT": datetime.now()
                    }]
                await stock_service.save_history_bulk(history_data)
                logger.debug(f"[DAY COLLECT] 데이터 저장 완료: {code}")

        except Exception as e:
            logger.error(f"[DAY COLLECT] 데이터 수집 실패 ({code}): {e}")
            raise


def _fire_trade_notification(
    user_id: str, swing, prev_signal: int, st_code: str,
    prev_hold_qty: int = 0, current_price: float = 0
):
    """SIGNAL 변경에 따른 푸쉬 알림 (fire-and-forget)

    전량 매도는 reset_cycle이 HOLD_QTY/ENTRY_PRICE를 지우므로 swing에서 체결 정보를
    읽을 수 없다. 매도 직전 수량(prev_hold_qty)과 평가 시점 현재가를 인자로 받는다.
    """
    new_signal = swing.SIGNAL
    entry_price = float(swing.ENTRY_PRICE) if swing.ENTRY_PRICE else 0
    hold_qty = swing.HOLD_QTY or 0

    # 매수 체결 (SIGNAL 0→1)
    if new_signal == 1 and prev_signal == 0:
        task = asyncio.create_task(
            PushNotificationService.send_trade_notification(
                user_id=user_id,
                noti_type="TRADE",
                st_code=st_code,
                qty=hold_qty,
                price=entry_price,
                reasons=["매수 완료"],
            )
        )
        task.add_done_callback(_on_notification_done)

    # 1차 익절 (SIGNAL 1→2)
    elif new_signal == 2 and prev_signal == 1:
        # transition_to_partial이 HOLD_QTY에서 매도분을 차감하므로 매도 수량은 차이로 구한다
        # (swing.HOLD_QTY를 그대로 쓰면 '잔여 수량'을 매도 수량으로 표기하게 된다)
        sold_qty = max(0, prev_hold_qty - hold_qty)
        task = asyncio.create_task(
            PushNotificationService.send_trade_notification(
                user_id=user_id,
                noti_type="TRADE",
                st_code=st_code,
                qty=sold_qty,
                price=current_price or entry_price,
                reasons=["1차 익절 50% 매도 완료"],
            )
        )
        task.add_done_callback(_on_notification_done)

    # 전량 매도 (SIGNAL 1,2→3)
    # reset_cycle은 항상 SIGNAL=3(수급 안정화 대기)으로 전이한다. 0을 기대하면
    # 조건이 영원히 성립하지 않아 전량매도 알림이 조용히 누락된다.
    elif new_signal == 3 and prev_signal in (1, 2):
        reason = "2차 익절 전량 매도 완료" if prev_signal == 2 else "전량 매도 완료"
        task = asyncio.create_task(
            PushNotificationService.send_trade_notification(
                user_id=user_id,
                noti_type="TRADE",
                st_code=st_code,
                qty=prev_hold_qty,
                price=current_price or entry_price,
                reasons=[reason],
            )
        )
        task.add_done_callback(_on_notification_done)


def _on_notification_done(task: asyncio.Task):
    """알림 태스크 완료 콜백 — 예외 로깅"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.warning(f"푸쉬 알림 전송 실패: {exc}")


async def us_trade_job():
    """미국 장 매매 신호 확인 및 실행 (5분 단위) — 해외 종목만"""
    if not _trading_allowed("NAS", "US BATCH"):
        return

    db = await Database.get_session()
    try:
        swing_service = SwingService(db)
        redis_client = await Redis.get_connection()
        swing_list = await swing_service.get_active_overseas_swings()
        logger.info(f"[US BATCH START] 활성 해외 스윙 수: {len(swing_list)}")
    except Exception as e:
        logger.error(f"us_trade_job 스윙 목록 조회 실패: {e}", exc_info=True)
        return
    finally:
        await db.close()


    tasks = [process_single_swing(swing_row, redis_client) for swing_row in swing_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    success_count = sum(1 for r in results if not isinstance(r, Exception))
    error_count = len(results) - success_count
    logger.info(
        f"[US BATCH END] 배치 작업 완료 - "
        f"성공: {success_count}, 실패: {error_count}, 총: {len(results)}"
    )


async def us_ema_cache_warmup_job():
    """해외 종목 지표 캐시 워밍업 (미국 장 시작 전)"""
    db = await Database.get_session()
    try:
        redis_client = await Redis.get_connection()
        swing_service = SwingService(db)
        result = await swing_service.warmup_ema_cache(redis_client, scope="overseas")
        logger.info(f"해외 지표 캐시 워밍업 결과: {result}")
    except Exception as e:
        logger.error(f"해외 지표 캐시 워밍업 실패: {e}", exc_info=True)
    finally:
        await db.close()


async def ema_cache_warmup_job():
    """
    국내 지표 캐시 워밍업 배치 (스케줄러에서 호출)

    - 실행 시점: 매일 08:29 KST (국내장 시작 전)
    - 대상: SWING_TRADE.USE_YN = 'Y'인 국내 종목 (미국은 us_ema_cache_warmup_job이 담당)
    - 작업: 과거 3년 데이터로 지표 계산 → Redis 저장
    - 저장 지표: EMA20, ADX, +DI, -DI, ATR, OBV-Z
    """
    db = await Database.get_session()

    try:
        redis_client = await Redis.get_connection()
        swing_service = SwingService(db)

        result = await swing_service.warmup_ema_cache(redis_client, scope="domestic")
        logger.info(f"국내 지표 캐시 워밍업 결과: {result}")

    except Exception as e:
        logger.error(f"지표 캐시 워밍업 실패: {e}", exc_info=True)
    finally:
        await db.close()
