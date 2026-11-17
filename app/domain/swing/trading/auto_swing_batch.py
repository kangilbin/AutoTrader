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
"""
import logging
import asyncio
from datetime import datetime
from decimal import Decimal
from app.domain.swing.indicators import TechnicalIndicators
from app.external.kis_api import get_target_price, get_inquire_price
from app.common.database import Database
from app.domain.swing.service import SwingService
from app.domain.stock.service import StockService
from .trading_strategy_factory import TradingStrategyFactory
from app.common.redis import Redis

logger = logging.getLogger(__name__)

# ===== 실시간 거래 동시 실행 제어 =====
_TRADE_SEMAPHORE = asyncio.Semaphore(5)  # 동시에 최대 5개 종목 처리


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
            process_single_swing(swing, swing_service, redis_client)
            for swing in swing_list
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
    swing,
    swing_service: SwingService,
    redis_client
):
    """
    개별 스윙 매매 신호 처리 (세마포어로 동시 실행 제어)

    Args:
        swing: SWING_TRADE 레코드
        swing_service: SwingService 인스턴스
        redis_client: Redis 클라이언트
    """
    async with _TRADE_SEMAPHORE:
        try:
            swing_id = swing.SWING_ID
            st_code = swing.ST_CODE
            swing_type = swing.SWING_TYPE if hasattr(swing, 'SWING_TYPE') else 'S'

            # SWING_TYPE에 따른 전략 선택
            strategy = TradingStrategyFactory.get_strategy(swing_type)

            # === 1. 데이터 수집 ===

            # 1.1 지표 캐시 확인 (필수)
            cached_indicators = await strategy.get_cached_indicators(redis_client, st_code)

            if not cached_indicators:
                logger.warning(f"[{st_code}] 등록된 캐시 정보가 없습니다.")
                return

            # 1.2 현재가 조회
            current_price_data = await get_inquire_price("mgnt", st_code, swing_service.db)

            if not current_price_data:
                logger.warning(f"[{st_code}] 현재가 조회 실패")
                return

            current_price = Decimal(str(current_price_data.get("stck_prpr", 0)))
            current_high = Decimal(str(current_price_data.get("stck_hgpr", current_price)))
            current_low = Decimal(str(current_price_data.get("stck_lwpr", current_price)))
            acml_vol = int(current_price_data.get("acml_vol", 0))

            # frgn_ntby_qty = int(current_price_data.get("frgn_ntby_qty", 0))  # 외국인 순매수 수량
            # prdy_vrss_vol_rate = float(current_price_data.get("prdy_vrss_vol_rate", 100))  # 저일 대비 거래량 비율
            # prdy_ctrt = float(current_price_data.get("prdy_ctrt", 0))  # 전일 대비율

            # 1.3 실시간 지표 증분 계산
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

            # === 2. 전략 클래스에 처리 위임 ===
            result = await strategy.process_trading_cycle(
                swing=swing,
                redis_client=redis_client,
                cached_indicators=cached_indicators,
                current_price_data=current_price_data,
                db=swing_service.db
            )

            # === 3. SIGNAL 상태 업데이트 ===
            if result["updated"]:
                update_data = {
                    "SIGNAL": result["new_signal"],
                    "ENTRY_PRICE": result["entry_price"] if result["entry_price"] > 0 else None,
                    "HOLD_QTY": result["hold_qty"],
                    "PEAK_PRICE": result["peak_price"] if result.get("peak_price", 0) > 0 else None,
                    "MOD_DT": datetime.now()
                }
                await swing_service.update_swing(swing_id, update_data)
                logger.info(
                    f"[{swing_id} - 코드: {st_code}] 상태 업데이트 완료: "
                    f"SIGNAL={result['new_signal']}, "
                    f"ENTRY_PRICE={result['entry_price']:,}원, "
                    f"HOLD_QTY={result['hold_qty']}주"
                )

        except Exception as e:
            logger.error(
                f"스윙 처리 실패 (SWING_ID={swing.SWING_ID}, ST_CODE={swing.ST_CODE}): {e}",
                exc_info=True
            )


async def day_collect_job():
    """
    일별 데이터 수집 (장 마감 후 15:35)

    작업: 활성 스윙의 당일 OHLCV 데이터 수집
    """
    logger.info("[DAY COLLECT] 데이터 수집 시작")
    db = await Database.get_session()

    try:
        stock_service = StockService(db)

        # 데이터 수집 (DATA_YN='Y'인 종목 대상)
        data_target_stocks = await stock_service.get_data_target_stocks()
        logger.info(f"[DAY COLLECT] 데이터 수집 대상 종목 수: {len(data_target_stocks)}")

        for stock in data_target_stocks:
            try:
                code = stock.ST_CODE
                mrkt_code = stock.MRKT_CODE
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
                logger.error(f"[DAY COLLECT] 데이터 수집 실패 ({stock.ST_CODE}): {e}")

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

