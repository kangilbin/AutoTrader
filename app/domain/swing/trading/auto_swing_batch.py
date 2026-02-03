"""
스윙 매매 배치 작업 (5분 간격)
단일 20EMA 전략 + 분할 매수/매도 지원

SIGNAL 상태:
- 0: 대기 (포지션 없음)
- 1: 1차 매수 완료 (부분 포지션)
- 2: 2차 매수 완료 (전체 포지션)
- 3: 1차 매도 완료 (부분 청산)
- → 0: 2차 매도 후 사이클 완료
"""
import logging
from datetime import datetime, timedelta
import pandas as pd
from decimal import Decimal

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
        stock_service = StockService(db)

        # Redis 연결
        redis_client = await Redis.get_connection()

        # 활성화된 스윙 목록 조회
        swing_list = await swing_service.get_active_swings()

        logger.info(f"[BATCH START] 활성 스윙 수: {len(swing_list)}")

        for swing in swing_list:
            try:
                await process_single_swing(
                    swing,
                    stock_service,
                    swing_service,
                    redis_client,
                    db
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
    stock_service: StockService,
    swing_service: SwingService,
    redis_client,
    db
):
    """
    개별 스윙 매매 신호 처리 + 분할 매수/매도 실행

    SIGNAL 상태 머신:
    - 0 + BUY신호 → 1차 매수 실행 → SIGNAL=1
    - 1 + BUY유지 → 2차 매수 실행 → SIGNAL=2
    - 1/2 + SELL신호 → 1차 매도 실행 → SIGNAL=3
    - 3 → 2차 매도 실행 → SIGNAL=0

    Args:
        swing: SWING_TRADE 레코드 (Raw SQL result with USER_ID, API_KEY, SECRET_KEY)
        stock_service: StockService 인스턴스
        swing_service: SwingService 인스턴스
        redis_client: Redis 클라이언트
        db: Database session
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

    # SWING_TYPE에 따른 전략 선택
    strategy = TradingStrategyFactory.get_strategy(swing_type)

    logger.info(
        f"[{st_code}] 처리 시작 (SWING_TYPE={swing_type}, SIGNAL={current_signal}, "
        f"ENTRY_PRICE={entry_price:,}원, HOLD_QTY={hold_qty}주, 전략={strategy.__name__})"
    )

    # === 1. 데이터 수집 ===

    # 1.1 주가 데이터 조회 (과거 120일)
    start_date = datetime.now() - timedelta(days=120)
    price_history = await stock_service.get_stock_history(st_code, start_date)

    if not price_history:
        logger.warning(f"[{st_code}] 주가 데이터 없음")
        return

    df = pd.DataFrame(price_history)

    # 1.2 현재가 조회
    try:
        current_price_data = await get_inquire_price("mgnt", st_code)

        if not current_price_data:
            logger.warning(f"[{st_code}] 현재가 조회 실패")
            return

        current_price = Decimal(str(current_price_data.get("stck_prpr", 0)))

        if current_price == 0:
            logger.warning(f"[{st_code}] 현재가가 0입니다")
            return

        # 당일 데이터로 DataFrame 업데이트
        today_data = {
            "ST_CODE": st_code,
            "STCK_BSOP_DATE": datetime.now().strftime('%Y%m%d'),
            "STCK_OPRC": current_price_data.get("stck_oprc", current_price),
            "STCK_HGPR": current_price_data.get("stck_hgpr", current_price),
            "STCK_LWPR": current_price_data.get("stck_lwpr", current_price),
            "STCK_CLPR": current_price,
            "ACML_VOL": current_price_data.get("acml_vol", 0)
        }

        df = pd.concat([df, pd.DataFrame([today_data])], ignore_index=True)
        df = df.drop_duplicates(subset=['STCK_BSOP_DATE'], keep='last')

    except Exception as e:
        logger.error(f"[{st_code}] 현재가 조회 실패: {e}")
        return

    # 1.3 외국인/기관 순매수 데이터
    frgn_ntby_qty = int(current_price_data.get("frgn_ntby_qty", 0))
    pgtr_ntby_qty = int(current_price_data.get("pgtr_ntby_qty", 0))
    acml_vol = int(today_data.get("ACML_VOL", 0))
    prdy_vrss_vol_rate = float(current_price_data.get("prdy_vrss_vol_rate", 100))
    prdy_ctrt = float(current_price_data.get("prdy_ctrt", 0))

    # === 2. SIGNAL 상태별 처리 ===

    new_signal = current_signal

    # ------------------------------------------
    # SIGNAL 0: 대기 상태 - 1차 매수 신호 확인
    # ------------------------------------------
    if current_signal == 0:
        entry_result = await strategy.check_entry_signal(
            redis_client=redis_client,
            symbol=st_code,
            df=df,
            current_price=current_price,
            frgn_ntby_qty=frgn_ntby_qty,
            pgtr_ntby_qty=pgtr_ntby_qty,
            acml_vol=acml_vol,
            prdy_vrss_vol_rate=prdy_vrss_vol_rate,
            prdy_ctrt=prdy_ctrt
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
                    logger.info(f"[{st_code}] 1차 매수 완료: 평단가={entry_price:,}원, 수량={hold_qty}주")
                else:
                    logger.error(f"[{st_code}] 1차 매수 실패: {order_result.get('reason')}")
            else:
                logger.warning(f"[{st_code}] USER_ID 없음, 주문 실행 불가")
        else:
            logger.debug(f"[{st_code}] 진입 조건 대기 중")

    # ------------------------------------------
    # SIGNAL 1: 1차 매수 완료 - 2차 매수 또는 매도 확인
    # ------------------------------------------
    elif current_signal == 1:
        # 먼저 매도 신호 확인 (저장된 평단가 사용)
        exit_result = await strategy.check_exit_signal(
            redis_client=redis_client,
            position_id=swing_id,
            symbol=st_code,
            df=df,
            current_price=current_price,
            entry_price=Decimal(entry_price) if entry_price > 0 else current_price,
            frgn_ntby_qty=frgn_ntby_qty,
            pgtr_ntby_qty=pgtr_ntby_qty,
            acml_vol=acml_vol
        )

        if exit_result.get("action") == "SELL":
            # 1차 매도 실행
            logger.info(f"[{st_code}] 1차 매도 신호 발생! (사유: {exit_result.get('reason')}, 평단가={entry_price:,}원)")

            if user_id:
                order_result = await SwingOrderExecutor.execute_first_sell(
                    user_id=user_id,
                    st_code=st_code,
                    sell_ratio=sell_ratio
                )

                if order_result.get("success"):
                    new_signal = 3
                    # 1차 매도 후 잔여 수량 업데이트
                    hold_qty = order_result.get("remaining", 0)
                    logger.info(f"[{st_code}] 1차 매도 완료: 잔여수량={hold_qty}주")
                else:
                    logger.error(f"[{st_code}] 1차 매도 실패: {order_result.get('reason')}")
            else:
                logger.warning(f"[{st_code}] USER_ID 없음, 주문 실행 불가")

        else:
            # 매도 신호 없으면 2차 매수 조건 확인
            entry_result = await strategy.check_second_buy_signal(
                db=db,
                redis_client=redis_client,
                swing_id=swing_id,
                symbol=st_code,
                df=df,
                entry_price=Decimal(entry_price) if entry_price > 0 else current_price,
                hold_qty=hold_qty,
                current_price=current_price,
                frgn_ntby_qty=frgn_ntby_qty,
                acml_vol=acml_vol,
                prdy_vrss_vol_rate=prdy_vrss_vol_rate
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
    # SIGNAL 2: 2차 매수 완료 - 매도 신호만 확인
    # ------------------------------------------
    elif current_signal == 2:
        # 저장된 평단가 사용
        exit_result = await strategy.check_exit_signal(
            redis_client=redis_client,
            position_id=swing_id,
            symbol=st_code,
            df=df,
            current_price=current_price,
            entry_price=Decimal(entry_price) if entry_price > 0 else current_price,
            frgn_ntby_qty=frgn_ntby_qty,
            pgtr_ntby_qty=pgtr_ntby_qty,
            acml_vol=acml_vol
        )

        if exit_result.get("action") == "SELL":
            logger.info(f"[{st_code}] 1차 매도 신호 발생! (사유: {exit_result.get('reason')}, 평단가={entry_price:,}원)")

            if user_id:
                order_result = await SwingOrderExecutor.execute_first_sell(
                    user_id=user_id,
                    st_code=st_code,
                    sell_ratio=sell_ratio
                )

                if order_result.get("success"):
                    new_signal = 3
                    # 1차 매도 후 잔여 수량 업데이트
                    hold_qty = order_result.get("remaining", 0)
                    logger.info(f"[{st_code}] 1차 매도 완료: 잔여수량={hold_qty}주")
                else:
                    logger.error(f"[{st_code}] 1차 매도 실패: {order_result.get('reason')}")
            else:
                logger.warning(f"[{st_code}] USER_ID 없음, 주문 실행 불가")
        else:
            logger.debug(f"[{st_code}] 보유 유지 (2차 매수 상태)")

    # ------------------------------------------
    # SIGNAL 3: 1차 매도 완료 - 2차 매도 실행
    # ------------------------------------------
    elif current_signal == 3:
        logger.info(f"[{st_code}] 2차 매도 실행 (잔량 전부: {hold_qty}주)")

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
                logger.info(f"[{st_code}] 2차 매도 완료, 사이클 종료 (포지션 청산)")
            else:
                logger.error(f"[{st_code}] 2차 매도 실패: {order_result.get('reason')}")
        else:
            logger.warning(f"[{st_code}] USER_ID 없음, 주문 실행 불가")

    # === 3. SIGNAL 상태 업데이트 (평단가, 보유수량 포함) ===

    if new_signal != current_signal:
        try:
            update_data = {
                "SIGNAL": new_signal,
                "ENTRY_PRICE": entry_price if entry_price > 0 else None,
                "HOLD_QTY": hold_qty,
                "MOD_DT": datetime.now()
            }
            await swing_service.update_swing(swing_id, update_data)
            await db.commit()
            logger.info(
                f"[{st_code}] 상태 업데이트: SIGNAL={current_signal}→{new_signal}, "
                f"ENTRY_PRICE={entry_price:,}원, HOLD_QTY={hold_qty}주"
            )
        except Exception as e:
            await db.rollback()
            logger.error(f"[{st_code}] 상태 업데이트 실패: {e}", exc_info=True)

    logger.info(f"[{st_code}] 처리 완료")


async def day_collect_job():
    """일별 데이터 수집 (장 마감 후) - 기존 로직 유지"""
    logger.info("데이터 수집 시작")
    db = await Database.get_session()
    try:
        swing_service = SwingService(db)
        stock_service = StockService(db)

        swing_list = await swing_service.get_active_swings()

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
                    logger.debug(f"데이터 저장 완료: {code}")

            except Exception as e:
                logger.error(f"데이터 수집 실패 ({swing.ST_CODE}): {e}")

        logger.info("데이터 수집 종료")

    except Exception as e:
        logger.error(f"day_collect_job 실패: {e}", exc_info=True)
    finally:
        await db.close()


async def ema_cache_warmup_job():
    """
    EMA 캐시 워밍업 배치 (스케줄러에서 호출)

    - 실행 시점: 매일 08:30 (장 시작 전)
    - 대상: SWING_TRADE.USE_YN = 'Y'인 종목
    - 작업: 과거 120일 데이터로 EMA20 초기 계산 → Redis 저장
    """
    db = await Database.get_session()

    try:
        redis_client = await Redis.get_connection()
        swing_service = SwingService(db)

        result = await swing_service.warmup_ema_cache(redis_client)
        logger.info(f"EMA 캐시 워밍업 결과: {result}")

    except Exception as e:
        logger.error(f"EMA 캐시 워밍업 실패: {e}", exc_info=True)
    finally:
        await db.close()
