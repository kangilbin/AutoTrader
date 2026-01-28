"""
스윙 매매 배치 작업 (5분 간격)
단일 20EMA 전략 + 분할 매수/매도 지원

SIGNAL 상태:
- 0: 대기 (포지션 없음)
- 1: 1차 매수 완료 (부분 포지션)
- 2: 2차 매수 완료 (전체 포지션)
- 3: 1차 손절 매도 완료 (2차 매도 대기)
- 4: 1차 매도 대기 (sell_ratio% 매도 대기, 종가 확정 → 다음날 시초 실행)
- 5: 2차 매도 대기 (전량 매도 대기, 종가 확정 → 다음날 시초 실행)

배치 스케줄:
- 08:30: ema_cache_warmup_job (EMA 캐시 워밍업)
- 09:00-09:55: morning_sell_job (시초 매도 실행, SIGNAL 4/5)
- 10:00-14:55: trade_job (장중 매수/손절 체크)
- 15:35: day_collect_job (일별 데이터 수집 + 종가 매도 신호 확정)
"""
import logging
from datetime import datetime
from decimal import Decimal
from app.domain.swing.indicators import TechnicalIndicators
from app.external.kis_api import get_target_price, get_inquire_price
from app.common.database import Database
from app.domain.swing.service import SwingService
from app.domain.stock.service import StockService
from .trading_strategy_factory import TradingStrategyFactory
from .order_executor import SwingOrderExecutor
from app.common.redis import Redis

logger = logging.getLogger(__name__)


async def trade_job():
    """
    매매 신호 확인 및 생성 (5분 단위)
    - 단일 20EMA 전략
    """
    db = await Database.get_session()

    try:
        swing_service = SwingService(db)

        # Redis 연결
        redis_client = await Redis.get_connection()

        # 활성화된 스윙 목록 조회
        swing_list = await swing_service.get_active_swings()

        logger.info(f"[BATCH START] 활성 스윙 수: {len(swing_list)}")

        for swing in swing_list:
            try:
                await process_single_swing(
                    swing,
                    swing_service,
                    redis_client,
                )
            except Exception as e:
                logger.error(
                    f"스윙 처리 실패 (SWING_ID={swing.SWING_ID}, ST_CODE={swing.ST_CODE}): {e}",
                    exc_info=True
                )
                continue

        logger.info("[BATCH END] 배치 작업 완료")

    except Exception as e:
        logger.error(f"trade_job 실패: {e}", exc_info=True)
    finally:
        await db.close()


async def process_single_swing(
    swing,
    swing_service: SwingService,
    redis_client
):
    """
    개별 스윙 매매 신호 처리 + 분할 매수/매도 실행

    SIGNAL 상태 머신:
    - 0 + BUY신호 → 1차 매수 실행 (buy_ratio%) → SIGNAL=1
    - 1 + BUY유지 → 2차 매수 실행 (100-buy_ratio%) → SIGNAL=2
    - 1/2 + 손절신호 → 1차 매도 실행 (sell_ratio%) → SIGNAL=3
    - 3 → 2차 매도 실행 (잔량 전부) → SIGNAL=0
    - 4/5 → 스킵 (morning_sell_job에서 처리)

    Args:
        swing: SWING_TRADE 레코드 (Raw SQL result with USER_ID, API_KEY, SECRET_KEY)
        swing_service: SwingService 인스턴스
        redis_client: Redis 클라이언트
    """
    swing_id = swing.SWING_ID
    st_code = swing.ST_CODE
    user_id = swing.USER_ID if hasattr(swing, 'USER_ID') else None
    current_signal = swing.SIGNAL if hasattr(swing, 'SIGNAL') else 0
    swing_type = swing.SWING_TYPE if hasattr(swing, 'SWING_TYPE') else 'A'
    init_amount = Decimal(str(swing.INIT_AMOUNT)) if hasattr(swing, 'INIT_AMOUNT') else Decimal(0)
    buy_ratio = swing.BUY_RATIO if hasattr(swing, 'BUY_RATIO') else 50
    sell_ratio = swing.SELL_RATIO if hasattr(swing, 'SELL_RATIO') else 50
    entry_price = int(swing.ENTRY_PRICE) if hasattr(swing, 'ENTRY_PRICE') and swing.ENTRY_PRICE else 0
    hold_qty = swing.HOLD_QTY if hasattr(swing, 'HOLD_QTY') and swing.HOLD_QTY else 0

    # SIGNAL 4 or 5는 morning_sell_job에서 처리 → 스킵
    if current_signal in (4, 5):
        logger.debug(f"[{st_code}] 매도 대기 상태(SIGNAL={current_signal}), trade_job에서 스킵")
        return

    # SWING_TYPE에 따른 전략 선택
    strategy = TradingStrategyFactory.get_strategy(swing_type)

    logger.info(
        f"[{swing_id} - 코드: {st_code}] 처리 시작 (SWING_TYPE={swing_type}, SIGNAL={current_signal}, "
        f"ENTRY_PRICE={entry_price:,}원, HOLD_QTY={hold_qty}주, 전략={strategy.__name__})"
    )

    # === 1. 데이터 수집 ===

    # 1.1 지표 캐시 확인 (필수)
    cached_indicators = await strategy.get_cached_indicators(redis_client, st_code)

    if not cached_indicators:
        logger.warning(f"[{st_code}] 등록된 캐시 정보가 없습니다.")
        return

    # 1.2 현재가 조회
    current_price_data = await get_inquire_price("mgnt", st_code)

    if not current_price_data:
        logger.warning(f"[{st_code}] 현재가 조회 실패")
        return

    current_price = Decimal(str(current_price_data.get("stck_prpr", 0)))             # 현재가
    current_high = Decimal(str(current_price_data.get("stck_hgpr", current_price)))  # 금일 고가
    current_low = Decimal(str(current_price_data.get("stck_lwpr", current_price)))   # 금일 저가
    acml_vol = int(current_price_data.get("acml_vol", 0))                            # 누적 거래량


    # 1.3 외국인 순매수 데이터
    frgn_ntby_qty = int(current_price_data.get("frgn_ntby_qty", 0))               # 외국인 순매수 수량
    prdy_vrss_vol_rate = float(current_price_data.get("prdy_vrss_vol_rate", 100)) # 저일 대비 거래량 비율
    prdy_ctrt = float(current_price_data.get("prdy_ctrt", 0))                     # 전일 대비율

    # 1.5 실시간 지표 증분 계산
    cached_indicators = TechnicalIndicators.enrich_cached_indicators_with_realtime(
        cached_indicators=cached_indicators,
        current_price=float(current_price),
        current_volume=acml_vol,
        current_high=float(current_high),
        current_low=float(current_low),
        ema_period=20,
        atr_period=14
    )
    logger.debug(
        f"[{st_code}] 실시간 지표 계산 완료: "
        f"EMA20={cached_indicators.get('realtime_ema20', 0):.2f}, "
        f"OBV_Z={cached_indicators.get('realtime_obv_z', 0):.2f}, "
        f"ATR={cached_indicators.get('realtime_atr', 0):.2f}, "
        f"ADX={cached_indicators.get('realtime_adx', 0):.1f}, "
        f"+DI={cached_indicators.get('realtime_plus_di', 0):.1f}, "
        f"-DI={cached_indicators.get('realtime_minus_di', 0):.1f}"
    )

    # === 2. SIGNAL 상태별 처리 ===

    new_signal = current_signal

    # ------------------------------------------
    # SIGNAL 0: 대기 상태 - 1차 매수 신호 확인
    # ------------------------------------------
    if current_signal == 0:
        entry_result = await strategy.check_entry_signal(
            redis_client=redis_client,
            swing_id=swing_id,
            symbol=st_code,
            current_price=current_price,
            frgn_ntby_qty=frgn_ntby_qty,
            acml_vol=acml_vol,
            prdy_vrss_vol_rate=prdy_vrss_vol_rate,
            prdy_ctrt=prdy_ctrt,
            cached_indicators=cached_indicators
        )

        if entry_result and entry_result.get("action") == "BUY":
            logger.info(f"[{st_code}] 1차 매수 신호 발생!")

            # 1차 매수 실행
            if user_id:
                order_result = await SwingOrderExecutor.execute_first_buy(
                    user_id=user_id,
                    st_code=st_code,
                    current_price=current_price,
                    init_amount=init_amount,
                    buy_ratio=buy_ratio
                )

                if order_result.get("success"):
                    new_signal = 1
                    # 평단가, 보유수량 저장
                    entry_price = order_result.get("avg_price", int(current_price))
                    hold_qty = order_result.get("qty", 0)

                    # 2차 매수 시간 필터용 Redis 키 생성 (20분 TTL)
                    await redis_client.setex(
                        f"first_buy_time:{swing_id}",
                        1200,  # 20분
                        datetime.now().isoformat()
                    )
                else:
                    logger.error(f"[{st_code}] 1차 매수 실패: {order_result.get('reason')}")
            else:
                logger.warning(f"[{st_code}] USER_ID 없음, 주문 실행 불가")
        else:
            logger.debug(f"[{st_code}] 진입 조건 대기 중")

    # ------------------------------------------
    # SIGNAL 1: 1차 매수 완료 - 손절 체크, 2차 매수 확인
    # ------------------------------------------
    elif current_signal == 1:
        # === 최우선: 장중 즉시 매도 체크 (고정 손절, EMA-ATR 손절, 수급 반전) ===
        if entry_price > 0:
            exit_result = await strategy.check_exit_signal(
                redis_client=redis_client,
                position_id=swing_id,
                symbol=st_code,
                current_price=current_price,
                entry_price=Decimal(entry_price),
                frgn_ntby_qty=frgn_ntby_qty,
                acml_vol=acml_vol,
                cached_indicators=cached_indicators
            )

            if exit_result and exit_result.get("action") == "SELL":
                logger.warning(
                    f"[{st_code}] 즉시 매도 신호 발동! 현재가={int(current_price):,}원, "
                    f"평단가={entry_price:,}원, 사유={exit_result.get('reason')}"
                )

                if user_id:
                    # 1차 매도 (sell_ratio%)
                    order_result = await SwingOrderExecutor.execute_first_sell(
                        user_id=user_id,
                        st_code=st_code,
                        sell_ratio=sell_ratio
                    )

                    if order_result.get("success"):
                        new_signal = 3  # 1차 매도 완료, 2차 매도 대기
                        hold_qty = order_result.get("remaining", 0)
                        logger.info(f"[{st_code}] 1차 손절 매도 완료 (sell_ratio={sell_ratio}%, 잔량={hold_qty}주)")
                    else:
                        logger.error(f"[{st_code}] 1차 손절 매도 실패: {order_result.get('reason')}")
                else:
                    logger.warning(f"[{st_code}] USER_ID 없음, 손절 주문 실행 불가")

            else:
                # 매도 신호 없으면 2차 매수 조건 확인
                entry_result = await strategy.check_second_buy_signal(
                    redis_client=redis_client,
                    swing_id=swing_id,
                    symbol=st_code,
                    entry_price=Decimal(entry_price) if entry_price > 0 else current_price,
                    hold_qty=hold_qty,
                    current_price=current_price,
                    frgn_ntby_qty=frgn_ntby_qty,
                    acml_vol=acml_vol,
                    prdy_vrss_vol_rate=prdy_vrss_vol_rate,
                    cached_indicators=cached_indicators
                )

                if entry_result and entry_result.get("action") == "BUY":
                    logger.info(f"[{st_code}] 2차 매수 신호 발생!")

                    if user_id:
                        order_result = await SwingOrderExecutor.execute_second_buy(
                            user_id=user_id,
                            st_code=st_code,
                            current_price=current_price,
                            init_amount=init_amount,
                            buy_ratio=buy_ratio
                        )

                        if order_result.get("success"):
                            new_signal = 2
                            # 2차 매수 후 평균 단가 재계산
                            new_avg_price = order_result.get("avg_price", int(current_price))
                            new_qty = order_result.get("qty", 0)
                            entry_price = SwingOrderExecutor.calculate_avg_entry_price(
                                prev_qty=hold_qty,
                                prev_price=entry_price,
                                new_qty=new_qty,
                                new_price=new_avg_price
                            )
                            hold_qty = hold_qty + new_qty
                            logger.info(f"[{st_code}] 2차 매수 완료: 새 평단가={entry_price:,}원, 총수량={hold_qty}주")
                        else:
                            logger.error(f"[{st_code}] 2차 매수 실패: {order_result.get('reason')}")
                    else:
                        logger.warning(f"[{st_code}] USER_ID 없음, 주문 실행 불가")
                else:
                    logger.debug(f"[{st_code}] 보유 유지 (1차 매수 상태)")

    # ------------------------------------------
    # SIGNAL 2: 2차 매수 완료 - 손절 체크만 (매도 신호는 day_collect_job에서)
    # ------------------------------------------
    elif current_signal == 2:
        # === 최우선: 장중 즉시 매도 체크 (고정 손절, EMA-ATR 손절, 수급 반전) ===
        if entry_price > 0:
            exit_result = await strategy.check_exit_signal(
                redis_client=redis_client,
                position_id=swing_id,
                symbol=st_code,
                current_price=current_price,
                entry_price=Decimal(entry_price),
                frgn_ntby_qty=frgn_ntby_qty,
                acml_vol=acml_vol,
                cached_indicators=cached_indicators
            )

            if exit_result and exit_result.get("action") == "SELL":
                logger.warning(
                    f"[{st_code}] 즉시 매도 신호 발동! 현재가={int(current_price):,}원, "
                    f"평단가={entry_price:,}원, 사유={exit_result.get('reason')}"
                )

                if user_id:
                    # 1차 매도 (sell_ratio%)
                    order_result = await SwingOrderExecutor.execute_first_sell(
                        user_id=user_id,
                        st_code=st_code,
                        sell_ratio=sell_ratio
                    )

                    if order_result.get("success"):
                        new_signal = 3  # 1차 매도 완료, 2차 매도 대기
                        hold_qty = order_result.get("remaining", 0)
                        logger.info(f"[{st_code}] 1차 손절 매도 완료 (sell_ratio={sell_ratio}%, 잔량={hold_qty}주)")
                    else:
                        logger.error(f"[{st_code}] 1차 손절 매도 실패: {order_result.get('reason')}")
                else:
                    logger.warning(f"[{st_code}] USER_ID 없음, 손절 주문 실행 불가")
            else:
                # 손절 아니면 보유 유지 (매도 신호는 종가 기준 day_collect_job에서 판단)
                logger.debug(f"[{st_code}] 보유 유지 (2차 매수 상태, 매도 신호는 종가 배치에서 확인)")
        else:
            logger.debug(f"[{st_code}] 보유 유지 (2차 매수 상태, 매도 신호는 종가 배치에서 확인)")

    # ------------------------------------------
    # SIGNAL 3: 1차 손절 매도 완료 - 2차 매도 실행 (잔량 전량)
    # ------------------------------------------
    elif current_signal == 3:
        logger.info(f"[{st_code}] 2차 손절 매도 실행 (잔량 전부: {hold_qty}주)")

        if user_id:
            order_result = await SwingOrderExecutor.execute_second_sell(
                user_id=user_id,
                st_code=st_code
            )

            if order_result.get("success"):
                new_signal = 0  # 사이클 완료, 대기 상태로 복귀
                # 포지션 청산 후 평단가/보유수량 초기화
                entry_price = 0
                hold_qty = 0
                logger.info(f"[{st_code}] 2차 손절 매도 완료, 사이클 종료 (포지션 청산)")
            else:
                logger.error(f"[{st_code}] 2차 손절 매도 실패: {order_result.get('reason')}")
        else:
            logger.warning(f"[{st_code}] USER_ID 없음, 주문 실행 불가")

    # === 3. SIGNAL 상태 업데이트 (평단가, 보유수량 포함) ===

    if new_signal != current_signal:
        update_data = {
            "SIGNAL": new_signal,
            "ENTRY_PRICE": entry_price if entry_price > 0 else None,
            "HOLD_QTY": hold_qty,
            "MOD_DT": datetime.now()
        }
        await swing_service.update_swing(swing_id, update_data)
        logger.info(
            f"[{swing_id} - 코드: {st_code}] 상태 업데이트: SIGNAL={current_signal}→{new_signal}, "
            f"ENTRY_PRICE={entry_price:,}원, HOLD_QTY={hold_qty}주"
        )


async def day_collect_job():
    """
    일별 데이터 수집 + 종가 매도 신호 확정 (장 마감 후 15:35)
    """
    logger.info("[DAY COLLECT] 데이터 수집 + 매도 신호 확정 시작")
    db = await Database.get_session()

    try:
        swing_service = SwingService(db)
        stock_service = StockService(db)

        swing_list = await swing_service.get_active_swings()

        # ========================================
        # 1단계: 데이터 수집
        # ========================================
        for swing in swing_list:
            try:
                code = swing.ST_CODE
                response = await get_target_price(code)

                if response:
                    history_data = [{
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
                logger.error(f"[DAY COLLECT] 데이터 수집 실패 ({swing.ST_CODE}): {e}")
        logger.info("[DAY COLLECT] 데이터 수집 완료")
    except Exception as e:
        logger.error(f"[DAY COLLECT] day_collect_job 실패: {e}", exc_info=True)
    finally:
        await db.close()


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

