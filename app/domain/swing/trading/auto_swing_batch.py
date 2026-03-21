"""
스윙 매매 배치 작업 (5분 간격)
단일 20EMA 전략 + 분할 매수/매도 지원

SIGNAL 상태:
- 0: 대기 (포지션 없음)
- 1: 1차 매수 완료 (부분 포지션)
- 2: 2차 매수 완료 (전체 포지션)
- 3: 1차 손절 매도 완료 (2차 매도 대기)

배치 스케줄:
- 08:30: ema_cache_warmup_job (EMA 캐시 워밍업)
- 09:00-14:55: trade_job (장중 매수/손절 체크)
- 15:35: day_collect_job (일별 데이터 수집)

오케스트레이션 패턴:
- Strategy: 신호 판단만 (check_entry_signal, check_exit_signal 등)
- Entity (SwingTrade): 상태 전환 (transition_to_first_buy, reset_cycle 등)
- OrderExecutor: 주문 실행 (execute_buy_with_partial, execute_sell_with_partial)
- Orchestrator (이 파일): 위 세 계층을 조율
"""
import logging
import asyncio
from datetime import datetime
from decimal import Decimal
from app.domain.swing.indicators import TechnicalIndicators
from app.domain.swing.repository import SwingRepository
from app.external.kis_api import get_target_price, get_inquire_price
from app.common.database import Database
from app.domain.swing.service import SwingService
from app.domain.stock.service import StockService
from app.domain.trade_history import TradeHistoryService
from .order_executor import SwingOrderExecutor
from .trading_strategy_factory import TradingStrategyFactory
from app.common.redis import Redis
from app.domain.notification.service import PushNotificationService

logger = logging.getLogger(__name__)

# ===== 동시 실행 제어 =====
_SEMAPHORE = asyncio.Semaphore(5)  # 동시에 최대 5개 종목 처리


async def trade_job():
    """
    매매 신호 확인 및 생성 (5분 단위)
    - 단일 20EMA 전략
    - 병렬 처리: 최대 5개 종목 동시 실행
    """
    db = await Database.get_session()

    try:
        swing_service = SwingService(db)

        # Redis 연결
        redis_client = await Redis.get_connection()

        # 활성화된 스윙 목록 조회
        swing_list = await swing_service.get_active_swings()

        logger.info(f"[BATCH START] 활성 스윙 수: {len(swing_list)}")

        # 병렬 처리: asyncio.gather로 모든 스윙 동시 실행
        tasks = [
            process_single_swing(swing_row, swing_service, redis_client)
            for swing_row in swing_list
        ]

        # return_exceptions=True로 설정하여 일부 실패해도 전체 진행
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 결과 로깅
        success_count = sum(1 for r in results if not isinstance(r, Exception))
        error_count = len(results) - success_count

        logger.info(
            f"[BATCH END] 배치 작업 완료 - "
            f"성공: {success_count}, 실패: {error_count}, 총: {len(results)}"
        )

    except Exception as e:
        logger.error(f"trade_job 실패: {e}", exc_info=True)
    finally:
        await db.close()


async def process_single_swing(
    swing_row,
    swing_service: SwingService,
    redis_client
):
    """
    개별 스윙 매매 오케스트레이터 (세마포어로 동시 실행 제어)

    Strategy(신호 판단) + Entity(상태 전환) + OrderExecutor(주문 실행) 조율

    Args:
        swing_row: SWING_TRADE 조인 결과 (USER_ID, API_KEY, SECRET_KEY 포함)
        swing_service: SwingService 인스턴스
        redis_client: Redis 클라이언트
    """
    async with _SEMAPHORE:
        try:
            swing_id = swing_row.SWING_ID
            st_code = swing_row.ST_CODE
            user_id = swing_row.USER_ID if hasattr(swing_row, 'USER_ID') else None
            swing_type = swing_row.SWING_TYPE if hasattr(swing_row, 'SWING_TYPE') else 'S'

            # ORM 엔티티 로드 (Entity 비즈니스 로직 메서드 사용)
            swing = await swing_service.repo.find_by_id(swing_id)
            if not swing:
                logger.warning(f"[{swing_id}] 스윙 엔티티 로드 실패")
                return

            # 전략 선택
            strategy = TradingStrategyFactory.get_strategy(swing_type)

            # === 1. 데이터 수집 ===
            cached_indicators = await strategy.get_cached_indicators(redis_client, st_code)
            if not cached_indicators:
                logger.warning(f"[{st_code}] 등록된 캐시 정보가 없습니다.")
                return

            current_price_data = await get_inquire_price("mgnt", st_code, swing_service.db)
            if not current_price_data:
                logger.warning(f"[{st_code}] 현재가 조회 실패")
                return

            current_price = Decimal(str(current_price_data.get("stck_prpr", 0)))
            current_high = Decimal(str(current_price_data.get("stck_hgpr", current_price)))
            current_low = Decimal(str(current_price_data.get("stck_lwpr", current_price)))
            acml_vol = int(current_price_data.get("acml_vol", 0))
            frgn_ntby_qty = int(current_price_data.get("frgn_ntby_qty", 0))
            prdy_vrss_vol_rate = float(current_price_data.get("prdy_vrss_vol_rate", 100))
            prdy_ctrt = float(current_price_data.get("prdy_ctrt", 0))

            # 실시간 지표 증분 계산
            cached_indicators = TechnicalIndicators.enrich_cached_indicators_with_realtime(
                cached_indicators=cached_indicators,
                current_price=float(current_price),
                current_volume=acml_vol,
                current_high=float(current_high),
                current_low=float(current_low),
                ema_period=20,
                atr_period=14
            )

            avg_daily_amount = cached_indicators["avg_daily_amount"]
            db = swing_service.db

            # === 2. 부분 체결 진행 중 체크 (신호 로직보다 우선) ===
            partial_key = f"partial_exec:{swing_id}"
            partial_state_str = await redis_client.get(partial_key)

            if partial_state_str and user_id:
                entry_price = int(swing.ENTRY_PRICE) if swing.ENTRY_PRICE else 0
                hold_qty = swing.HOLD_QTY or 0
                prev_signal = swing.SIGNAL

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
                    db=db
                )

                # 부분 체결 결과로 Entity 상태 직접 업데이트
                if partial_result.get("entry_price"):
                    swing.ENTRY_PRICE = Decimal(partial_result["entry_price"])
                if partial_result.get("hold_qty") is not None:
                    swing.HOLD_QTY = partial_result["hold_qty"]

                if partial_result.get("completed") or partial_result.get("aborted"):
                    new_signal = partial_result.get("signal_on_complete", swing.SIGNAL)
                    if new_signal == 0:
                        swing.reset_cycle()
                    else:
                        swing.SIGNAL = new_signal
                        swing.MOD_DT = datetime.now()

                await db.flush()
                await db.commit()

                # 푸쉬 알림 (fire-and-forget)
                if user_id and swing.SIGNAL != prev_signal:
                    _fire_trade_notification(user_id, swing, prev_signal, st_code)

                return

            # === 3. PEAK_PRICE 갱신 (Entity 메서드) ===
            if swing.has_position():
                swing.update_peak_price(int(current_high))

            # 변경 전 SIGNAL 저장 (알림용)
            prev_signal = swing.SIGNAL

            # === 4. SIGNAL별 오케스트레이션 ===
            if swing.is_waiting():
                await _handle_signal_0(
                    swing, strategy, redis_client, db, user_id, st_code,
                    current_price, frgn_ntby_qty, acml_vol,
                    prdy_vrss_vol_rate, prdy_ctrt,
                    cached_indicators, avg_daily_amount
                )

            elif swing.is_first_buy_done():
                await _handle_signal_1(
                    swing, strategy, redis_client, db, user_id, st_code,
                    current_price, frgn_ntby_qty, acml_vol,
                    prdy_vrss_vol_rate,
                    cached_indicators, avg_daily_amount
                )

            elif swing.is_second_buy_done():
                await _handle_signal_2(
                    swing, strategy, redis_client, db, user_id, st_code,
                    current_price, frgn_ntby_qty, acml_vol,
                    cached_indicators, avg_daily_amount
                )

            elif swing.is_primary_sold():
                await _handle_signal_3(
                    swing, strategy, redis_client, db, user_id, st_code,
                    current_price, frgn_ntby_qty, acml_vol,
                    prdy_vrss_vol_rate, prdy_ctrt,
                    cached_indicators, avg_daily_amount
                )

            # === 5. Entity 변경사항 저장 ===
            await db.flush()
            await db.commit()

            # === 6. 푸쉬 알림 (fire-and-forget) ===
            if user_id and swing.SIGNAL != prev_signal:
                _fire_trade_notification(user_id, swing, prev_signal, st_code)

        except Exception as e:
            logger.error(
                f"스윙 처리 실패 (SWING_ID={swing_row.SWING_ID}, ST_CODE={swing_row.ST_CODE}): {e}",
                exc_info=True
            )


# ==================== SIGNAL 핸들러 ====================


async def _handle_signal_0(
    swing, strategy, redis_client, db, user_id, st_code,
    current_price, frgn_ntby_qty, acml_vol,
    prdy_vrss_vol_rate, prdy_ctrt,
    cached_indicators, avg_daily_amount
):
    """SIGNAL 0: 대기 상태 → 1차 매수 신호 확인"""
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

    target_amount = Decimal(str(swing.INIT_AMOUNT)) * Decimal(swing.BUY_RATIO) / Decimal(100)
    order_result = await SwingOrderExecutor.execute_buy_with_partial(
        redis_client=redis_client,
        swing_id=swing.SWING_ID,
        user_id=user_id,
        st_code=st_code,
        current_price=current_price,
        target_amount=target_amount,
        avg_daily_amount=avg_daily_amount,
        signal_on_complete=1,
        db=db
    )

    if not order_result.get("success"):
        logger.error(f"[{st_code}] 1차 매수 실패: {order_result.get('reason')}")
        return

    # Entity 상태 전환
    avg_price = order_result.get("avg_price", int(current_price))
    qty = order_result.get("qty", 0)

    if order_result.get("completed", True):
        swing.transition_to_first_buy(avg_price, qty, int(current_price))
    else:
        # 부분 체결: 임시 상태 업데이트 (전환은 아직 아님)
        swing.ENTRY_PRICE = Decimal(avg_price)
        swing.HOLD_QTY = qty
        swing.MOD_DT = datetime.now()

    # 거래 내역 저장
    reasons = entry_result.get("reasons", ["1차 매수"]).copy()
    reasons.append(f"{swing.BUY_RATIO}%")
    trade_service = TradeHistoryService(db)
    await trade_service.record_trade(
        swing_id=swing.SWING_ID,
        trade_type="B",
        order_result=order_result,
        reasons=reasons
    )

    # 2차 매수 시간 필터용 Redis 키 (20분 TTL)
    await redis_client.setex(f"first_buy_time:{swing.SWING_ID}", 1200, datetime.now().isoformat())


async def _handle_signal_1(
    swing, strategy, redis_client, db, user_id, st_code,
    current_price, frgn_ntby_qty, acml_vol,
    prdy_vrss_vol_rate,
    cached_indicators, avg_daily_amount
):
    """SIGNAL 1: 1차 매수 완료 → 손절/trailing stop/2차 매수 확인"""
    entry_price = int(swing.ENTRY_PRICE) if swing.ENTRY_PRICE else 0
    hold_qty = swing.HOLD_QTY or 0

    if entry_price <= 0:
        return

    # 1. 손절 신호 체크
    exit_result = await strategy.check_exit_signal(
        redis_client=redis_client,
        position_id=swing.SWING_ID,
        symbol=st_code,
        current_price=current_price,
        entry_price=Decimal(entry_price),
        frgn_ntby_qty=frgn_ntby_qty,
        acml_vol=acml_vol,
        cached_indicators=cached_indicators
    )

    if exit_result and exit_result.get("action") == "SELL":
        await _execute_full_sell(
            swing, redis_client, db, user_id, st_code,
            current_price, hold_qty, avg_daily_amount,
            exit_result.get("reasons", ["손절"]),
            f"[{user_id} - 주식: {st_code}] 손절 전량 매도 완료, 사이클 종료"
        )
        return

    # 2. 장중 trailing stop 체크
    ts_result = await strategy.check_trailing_stop_signal(
        symbol=st_code,
        current_price=current_price,
        peak_price=int(swing.PEAK_PRICE) if swing.PEAK_PRICE else 0,
        signal=swing.SIGNAL,
        cached_indicators=cached_indicators
    )

    if ts_result and ts_result.get("action") == "SELL_PRIMARY":
        await _execute_primary_sell(
            swing, redis_client, db, user_id, st_code,
            current_price, hold_qty, avg_daily_amount,
            ts_result.get("reasons", ["1차 분할 매도"])
        )
        return

    if ts_result and ts_result.get("action") == "SELL_ALL":
        await _execute_full_sell(
            swing, redis_client, db, user_id, st_code,
            current_price, hold_qty, avg_daily_amount,
            ts_result.get("reasons", ["전량 매도"]),
            f"[{user_id} - 주식: {st_code}] 전량 매도 완료, 사이클 종료"
        )
        return

    # 3. 2차 매수 신호 확인
    entry_result = await strategy.check_second_buy_signal(
        redis_client=redis_client,
        swing_id=swing.SWING_ID,
        symbol=st_code,
        entry_price=Decimal(entry_price) if entry_price > 0 else current_price,
        hold_qty=hold_qty,
        current_price=current_price,
        frgn_ntby_qty=frgn_ntby_qty,
        acml_vol=acml_vol,
        prdy_vrss_vol_rate=prdy_vrss_vol_rate,
        cached_indicators=cached_indicators
    )

    if not (entry_result and entry_result.get("action") == "BUY"):
        return

    if not user_id:
        logger.warning(f"[{st_code}] USER_ID 없음, 주문 실행 불가")
        return

    second_target_amount = Decimal(str(swing.INIT_AMOUNT)) * Decimal(100 - swing.BUY_RATIO) / Decimal(100)
    order_result = await SwingOrderExecutor.execute_buy_with_partial(
        redis_client=redis_client,
        swing_id=swing.SWING_ID,
        user_id=user_id,
        st_code=st_code,
        current_price=current_price,
        target_amount=second_target_amount,
        avg_daily_amount=avg_daily_amount,
        signal_on_complete=2,
        db=db
    )

    if not order_result.get("success"):
        logger.error(f"[{user_id} - 주식: {st_code}] 2차 매수 실패: {order_result.get('reason')}")
        return

    new_avg_price = order_result.get("avg_price", int(current_price))
    new_qty = order_result.get("qty", 0)
    combined_entry = SwingOrderExecutor.calculate_avg_entry_price(
        prev_qty=hold_qty, prev_price=entry_price,
        new_qty=new_qty, new_price=new_avg_price
    )

    if order_result.get("completed", True):
        swing.transition_to_second_buy(combined_entry, hold_qty + new_qty)
    else:
        swing.ENTRY_PRICE = Decimal(combined_entry)
        swing.HOLD_QTY = hold_qty + new_qty
        swing.MOD_DT = datetime.now()

    # 거래 내역 저장
    reasons = entry_result.get("reasons", ["2차 매수"]).copy()
    reasons.append(f"{100 - swing.BUY_RATIO}%")
    trade_service = TradeHistoryService(db)
    await trade_service.record_trade(
        swing_id=swing.SWING_ID,
        trade_type="B",
        order_result=order_result,
        reasons=reasons
    )
    logger.info(f"[{user_id} - 주식: {st_code}] 2차 매수 완료: 새 평단가={combined_entry:,}원, 총수량={hold_qty + new_qty}주")


async def _handle_signal_2(
    swing, strategy, redis_client, db, user_id, st_code,
    current_price, frgn_ntby_qty, acml_vol,
    cached_indicators, avg_daily_amount
):
    """SIGNAL 2: 2차 매수 완료 → 손절/trailing stop 확인"""
    entry_price = int(swing.ENTRY_PRICE) if swing.ENTRY_PRICE else 0
    hold_qty = swing.HOLD_QTY or 0

    if entry_price <= 0:
        return

    # 1. 손절 신호 체크
    exit_result = await strategy.check_exit_signal(
        redis_client=redis_client,
        position_id=swing.SWING_ID,
        symbol=st_code,
        current_price=current_price,
        entry_price=Decimal(entry_price),
        frgn_ntby_qty=frgn_ntby_qty,
        acml_vol=acml_vol,
        cached_indicators=cached_indicators
    )

    if exit_result and exit_result.get("action") == "SELL":
        await _execute_full_sell(
            swing, redis_client, db, user_id, st_code,
            current_price, hold_qty, avg_daily_amount,
            exit_result.get("reasons", ["손절"]),
            f"[{user_id} - 주식: {st_code}] 손절 매도 완료, 사이클 종료"
        )
        return

    # 2. 장중 trailing stop 체크
    ts_result = await strategy.check_trailing_stop_signal(
        symbol=st_code,
        current_price=current_price,
        peak_price=int(swing.PEAK_PRICE) if swing.PEAK_PRICE else 0,
        signal=swing.SIGNAL,
        cached_indicators=cached_indicators
    )

    if ts_result and ts_result.get("action") == "SELL_PRIMARY":
        await _execute_primary_sell(
            swing, redis_client, db, user_id, st_code,
            current_price, hold_qty, avg_daily_amount,
            ts_result.get("reasons", ["1차 분할 매도"])
        )

    elif ts_result and ts_result.get("action") == "SELL_ALL":
        await _execute_full_sell(
            swing, redis_client, db, user_id, st_code,
            current_price, hold_qty, avg_daily_amount,
            ts_result.get("reasons", ["전량 매도"]),
            f"[{user_id} - 주식: {st_code}] 전량 매도 완료, 사이클 종료"
        )


async def _handle_signal_3(
    swing, strategy, redis_client, db, user_id, st_code,
    current_price, frgn_ntby_qty, acml_vol,
    prdy_vrss_vol_rate, prdy_ctrt,
    cached_indicators, avg_daily_amount
):
    """SIGNAL 3: 1차 매도 완료 → 손절/재진입/2차 전량 매도 확인"""
    entry_price = int(swing.ENTRY_PRICE) if swing.ENTRY_PRICE else 0
    hold_qty = swing.HOLD_QTY or 0

    # 1. 손절 신호 체크
    if entry_price > 0:
        exit_result = await strategy.check_exit_signal(
            redis_client=redis_client,
            position_id=swing.SWING_ID,
            symbol=st_code,
            current_price=current_price,
            entry_price=Decimal(entry_price),
            frgn_ntby_qty=frgn_ntby_qty,
            acml_vol=acml_vol,
            cached_indicators=cached_indicators
        )

        if exit_result and exit_result.get("action") == "SELL":
            await _execute_full_sell(
                swing, redis_client, db, user_id, st_code,
                current_price, hold_qty, avg_daily_amount,
                exit_result.get("reasons", ["손절"]),
                f"[{user_id} - 주식: {st_code}] SIGNAL 3 손절 전량 매도 완료, 사이클 종료"
            )
            return

    # 2. 재진입 신호 체크
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

    if entry_result and entry_result.get("action") == "BUY":
        if not user_id:
            logger.warning(f"[{st_code}] USER_ID 없음, 재진입 주문 실행 불가")
            return

        reentry_target = Decimal(str(swing.INIT_AMOUNT)) * Decimal(swing.BUY_RATIO) / Decimal(100)
        order_result = await SwingOrderExecutor.execute_buy_with_partial(
            redis_client=redis_client,
            swing_id=swing.SWING_ID,
            user_id=user_id,
            st_code=st_code,
            current_price=current_price,
            target_amount=reentry_target,
            avg_daily_amount=avg_daily_amount,
            signal_on_complete=1,
            db=db
        )

        if not order_result.get("success"):
            logger.error(f"[{user_id} - 주식: {st_code}] 재진입 매수 실패: {order_result.get('reason')}")
            return

        new_avg_price = order_result.get("avg_price", int(current_price))
        new_qty = order_result.get("qty", 0)
        combined_entry = SwingOrderExecutor.calculate_avg_entry_price(
            prev_qty=hold_qty, prev_price=entry_price,
            new_qty=new_qty, new_price=new_avg_price
        )
        total_qty = hold_qty + new_qty

        if order_result.get("completed", True):
            swing.transition_to_reentry(combined_entry, total_qty, int(current_price))
        else:
            swing.ENTRY_PRICE = Decimal(combined_entry)
            swing.HOLD_QTY = total_qty
            swing.MOD_DT = datetime.now()

        # 거래 내역 저장
        trade_service = TradeHistoryService(db)
        await trade_service.record_trade(
            swing_id=swing.SWING_ID,
            trade_type="B",
            order_result=order_result,
            reasons=["재진입 매수", f"{swing.BUY_RATIO}%"]
        )

        # 2차 매수 시간 필터용 Redis 키 (20분 TTL)
        await redis_client.setex(f"first_buy_time:{swing.SWING_ID}", 1200, datetime.now().isoformat())
        logger.info(f"[{user_id} - 주식: {st_code}] 재진입 매수 완료: 평단가={combined_entry:,}원, 총수량={total_qty}주")
        return

    # 3. 장중 trailing stop (2차 전량 매도)
    ts_result = await strategy.check_trailing_stop_signal(
        symbol=st_code,
        current_price=current_price,
        peak_price=int(swing.PEAK_PRICE) if swing.PEAK_PRICE else 0,
        signal=swing.SIGNAL,
        cached_indicators=cached_indicators
    )

    if ts_result and ts_result.get("action") == "SELL_ALL":
        await _execute_full_sell(
            swing, redis_client, db, user_id, st_code,
            current_price, hold_qty, avg_daily_amount,
            ts_result.get("reasons", ["2차 전량 매도"]),
            f"[{user_id} - 주식: {st_code}] 2차 매도 완료, 사이클 종료"
        )


# ==================== 공통 매도 헬퍼 ====================


async def _execute_full_sell(
    swing, redis_client, db, user_id, st_code,
    current_price, hold_qty, avg_daily_amount,
    reasons, success_log_msg
):
    """전량 매도 실행 → Entity reset_cycle()"""
    if not user_id:
        logger.warning(f"[{st_code}] USER_ID 없음, 매도 주문 실행 불가")
        return

    order_result = await SwingOrderExecutor.execute_sell_with_partial(
        redis_client=redis_client,
        swing_id=swing.SWING_ID,
        user_id=user_id,
        st_code=st_code,
        current_price=current_price,
        target_qty=hold_qty,
        avg_daily_amount=avg_daily_amount,
        signal_on_complete=0,
        db=db
    )

    if not order_result.get("success"):
        logger.error(f"[{st_code}] 매도 실패: {order_result.get('reason')}")
        return

    if order_result.get("completed", True):
        swing.reset_cycle()
    else:
        sold_qty = order_result.get("qty", 0)
        swing.update_hold_qty_partial(sold_qty)

    # 거래 내역 저장
    trade_service = TradeHistoryService(db)
    await trade_service.record_trade(
        swing_id=swing.SWING_ID,
        trade_type="S",
        order_result=order_result,
        reasons=reasons
    )
    logger.info(success_log_msg)


async def _execute_primary_sell(
    swing, redis_client, db, user_id, st_code,
    current_price, hold_qty, avg_daily_amount,
    reasons
):
    """1차 분할 매도 실행 → Entity transition_to_primary_sell()"""
    if not user_id:
        logger.warning(f"[{st_code}] USER_ID 없음, 매도 주문 실행 불가")
        return

    sell_qty = int(hold_qty * swing.SELL_RATIO / 100)
    order_result = await SwingOrderExecutor.execute_sell_with_partial(
        redis_client=redis_client,
        swing_id=swing.SWING_ID,
        user_id=user_id,
        st_code=st_code,
        current_price=current_price,
        target_qty=sell_qty,
        avg_daily_amount=avg_daily_amount,
        signal_on_complete=3,
        db=db
    )

    if not order_result.get("success"):
        logger.error(f"[{user_id} - 주식: {st_code}] 1차 분할 매도 실패: {order_result.get('reason')}")
        return

    sold_qty = order_result.get("qty", 0)
    remaining = hold_qty - sold_qty

    if order_result.get("completed", True):
        swing.transition_to_primary_sell(remaining)
    else:
        swing.update_hold_qty_partial(sold_qty)

    # 거래 내역 저장
    trade_service = TradeHistoryService(db)
    await trade_service.record_trade(
        swing_id=swing.SWING_ID,
        trade_type="S",
        order_result=order_result,
        reasons=reasons
    )
    logger.info(f"[{user_id} - 주식: {st_code}] 1차 분할 매도 완료 (sell_ratio={swing.SELL_RATIO}%, 잔량={remaining}주)")


# ==================== 기타 배치 작업 ====================


async def day_collect_job():
    """
    일별 데이터 수집 (장 마감 후 15:35)

    작업: 활성 스윙의 당일 OHLCV 데이터 수집
    병렬 처리: 최대 5개 종목 동시 실행
    """
    logger.info("[DAY COLLECT] 데이터 수집 시작")
    db = await Database.get_session()

    try:
        stock_service = StockService(db)

        # 데이터 수집 (DATA_YN='Y'인 종목 대상)
        data_target_stocks = await stock_service.get_data_target_stocks()
        logger.info(f"[DAY COLLECT] 데이터 수집 대상 종목 수: {len(data_target_stocks)}")

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
            f"[DAY COLLECT] 데이터 수집 완료 - "
            f"성공: {success_count}, 실패: {error_count}, 총: {len(results)}"
        )

    except Exception as e:
        logger.error(f"[DAY COLLECT] day_collect_job 실패: {e}", exc_info=True)
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

        try:
            response = await get_target_price(code)

            if response:
                history_data = [{
                    "MRKT_CODE": mrkt_code,
                    "ST_CODE": code,
                    "STCK_BSOP_DATE": datetime.now().strftime('%Y%m%d'),
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
    user_id: str, swing, prev_signal: int, st_code: str
):
    """SIGNAL 변경에 따른 푸쉬 알림 (fire-and-forget)"""
    new_signal = swing.SIGNAL
    entry_price = int(swing.ENTRY_PRICE) if swing.ENTRY_PRICE else 0
    hold_qty = swing.HOLD_QTY or 0

    # 매수 체결 (SIGNAL 증가: 0→1, 1→2)
    if new_signal in (1, 2) and prev_signal < new_signal:
        phase = new_signal
        asyncio.create_task(
            PushNotificationService.send_trade_notification(
                user_id=user_id,
                noti_type="TRADE",
                st_code=st_code,
                qty=hold_qty,
                price=entry_price,
                reasons=[f"{phase}차 매수 완료"],
            )
        )

    # 1차 분할 매도 (SIGNAL 1,2 → 3)
    elif new_signal == 3 and prev_signal in (1, 2):
        asyncio.create_task(
            PushNotificationService.send_trade_notification(
                user_id=user_id,
                noti_type="TRADE",
                st_code=st_code,
                qty=hold_qty,
                price=entry_price,
                reasons=["1차 분할 매도 완료"],
            )
        )

    # 전량 매도 (SIGNAL → 0)
    elif new_signal == 0 and prev_signal in (1, 2, 3):
        asyncio.create_task(
            PushNotificationService.send_trade_notification(
                user_id=user_id,
                noti_type="TRADE",
                st_code=st_code,
                qty=0,
                price=entry_price,
                reasons=["전량 매도 완료"],
            )
        )


async def ema_cache_warmup_job():
    """
    지표 캐시 워밍업 배치 (스케줄러에서 호출)

    - 실행 시점: 매일 08:30 (장 시작 전)
    - 대상: SWING_TRADE.USE_YN = 'Y'인 종목
    - 작업: 과거 3년 데이터로 지표 계산 → Redis 저장
    - 저장 지표: EMA20, ADX, +DI, -DI, ATR, OBV-Z
    """
    db = await Database.get_session()

    try:
        redis_client = await Redis.get_connection()
        swing_service = SwingService(db)

        result = await swing_service.warmup_ema_cache(redis_client)
        logger.info(f"지표 캐시 워밍업 결과: {result}")

    except Exception as e:
        logger.error(f"지표 캐시 워밍업 실패: {e}", exc_info=True)
    finally:
        await db.close()
