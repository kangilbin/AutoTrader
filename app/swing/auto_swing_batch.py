"""
스윙 매매 배치 작업
"""
import asyncio
import logging
from datetime import datetime, timedelta
import pandas as pd

from app.external.kis_api import get_target_price
from app.swing.tech_analysis import ema_swing_signals
from app.core.security import decrypt
from app.common.database import Database
from app.swing.service import SwingService
from app.stock.service import StockService

logger = logging.getLogger(__name__)


async def trade_job():
    """매매 신호 확인 및 실행 (1시간 단위)"""
    db = await Database.get_session()
    try:
        swing_service = SwingService(db)
        stock_service = StockService(db)

        swing_list = await swing_service.get_active_swings()

        for swing in swing_list:
            logger.info(f"스윙 시작: {swing.SWING_ID}, {swing.ST_CODE}")

            # 이평선 데이터 조회
            start_date = datetime.now() - timedelta(days=swing.LONG_TERM * 2)
            price_days = await stock_service.get_stock_history(swing.ST_CODE, start_date)

            if not price_days:
                logger.warning(f"주가 데이터 없음: {swing.ST_CODE}")
                continue

            df = pd.DataFrame(price_days)

            # 이평선 신호 분석
            first_buy_signal, second_buy_signal, first_sell_signal, stop_loss_signal = ema_swing_signals(
                df, swing.SHORT_TERM, swing.MEDIUM_TERM, swing.LONG_TERM
            )

            if stop_loss_signal:
                logger.info("손절 신호 발생")
                # 매도 로직 실행
                # swing.SIGNAL = "0" 초기화
            else:
                if swing.SIGNAL == "0":  # 최초 상태
                    if first_buy_signal:
                        logger.info("단기-중기 매수 신호 발생")
                        # 매수 로직 실행
                        # swing.SIGNAL = "1"
                elif swing.SIGNAL == "1":  # 첫 매수 후
                    if second_buy_signal:
                        logger.info("중기-장기 매수 신호 발생")
                        # 매수 로직 실행
                        # swing.SIGNAL = "2"
                    elif first_sell_signal:
                        logger.info("단기-중기 매도 신호 발생")
                        # 첫 매수 후 매도 → 전량 매도
                        # swing.SIGNAL = "0" 초기화
                elif swing.SIGNAL == "2":  # 두 번째 매수 후
                    if first_sell_signal:
                        logger.info("단기-중기 매도 신호 발생")
                        # 두 번째 매수 후 매도 → 전량 매도
                        # swing.SIGNAL = "3"

            logger.info(f"스윙 완료: {swing.SWING_ID}")

    except Exception as e:
        logger.error(f"trade_job 실패: {e}", exc_info=True)
    finally:
        await db.close()


async def day_collect_job():
    """일별 데이터 수집 (장 마감 후)"""
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