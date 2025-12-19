"""
스윙 매매 배치 작업 (5분 간격)
TYPE A (안정 진입) 및 TYPE B (모멘텀 추격) 전략 지원
"""
import logging
from datetime import datetime, timedelta
import pandas as pd
from decimal import Decimal

from app.external.kis_api import get_target_price
from app.common.database import Database
from app.domain.swing.service import SwingService
from app.domain.stock.service import StockService
from app.domain.swing.strategies.type_a_strategy import TypeAStrategy
from app.domain.swing.strategies.type_b_strategy import TypeBStrategy
from app.domain.swing.mock_data_service import MockMarketDataService

logger = logging.getLogger(__name__)


async def trade_job():
    """
    매매 신호 확인 및 생성 (5분 단위)
    - TYPE A: 안정적 진입 전략
    - TYPE B: 모멘텀 추격 전략
    """
    db = await Database.get_session()

    try:
        swing_service = SwingService(db)
        stock_service = StockService(db)
        mock_service = MockMarketDataService()

        # 활성화된 스윙 목록 조회
        swing_list = await swing_service.get_active_swings()

        logger.info(f"[BATCH START] 활성 스윙 수: {len(swing_list)}")

        for swing in swing_list:
            try:
                await process_single_swing(
                    swing,
                    stock_service,
                    swing_service,
                    mock_service,
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
    mock_service: MockMarketDataService,
    db
):
    """
    개별 스윙 매매 신호 처리

    Args:
        swing: SWING_TRADE 레코드 (Raw SQL result)
        stock_service: StockService 인스턴스
        swing_service: SwingService 인스턴스
        mock_service: MockMarketDataService 인스턴스
        db: Database session
    """
    swing_id = swing.SWING_ID
    st_code = swing.ST_CODE
    swing_type = swing.SWING_TYPE
    current_signal = swing.SIGNAL if hasattr(swing, 'SIGNAL') else 0

    logger.info(f"[{st_code}] 처리 시작 (TYPE={swing_type}, SIGNAL={current_signal})")

    # === 1. 데이터 수집 ===

    # 1.1 주가 데이터 조회 (과거 120일, 충분한 버퍼)
    start_date = datetime.now() - timedelta(days=120)
    price_history = await stock_service.get_stock_history(st_code, start_date)

    if not price_history:
        logger.warning(f"[{st_code}] 주가 데이터 없음")
        return

    df = pd.DataFrame(price_history)

    # 1.2 현재가 조회
    try:
        # KIS API를 통해 현재가 및 당일 데이터 조회
        # 관리자 계정 또는 해당 사용자의 API 키 사용
        # 여기서는 간단히 get_target_price 사용 (실제로는 get_inquire_price 권장)
        current_price_data = await get_target_price(st_code)

        if not current_price_data:
            logger.warning(f"[{st_code}] 현재가 조회 실패")
            return

        current_price = Decimal(str(current_price_data.get("stck_prpr", 0)))

        if current_price == 0:
            logger.warning(f"[{st_code}] 현재가가 0입니다")
            return

        # 당일 데이터로 DataFrame 업데이트 (최신 데이터 반영)
        today_data = {
            "ST_CODE": st_code,
            "STCK_BSOP_DATE": datetime.now().strftime('%Y%m%d'),
            "STCK_OPRC": current_price_data.get("stck_oprc", current_price),
            "STCK_HGPR": current_price_data.get("stck_hgpr", current_price),
            "STCK_LWPR": current_price_data.get("stck_lwpr", current_price),
            "STCK_CLPR": current_price,
            "ACML_VOL": current_price_data.get("acml_vol", 0)
        }

        # 기존 DataFrame에 오늘 데이터 추가 (중복 제거)
        df = pd.concat([df, pd.DataFrame([today_data])], ignore_index=True)
        df = df.drop_duplicates(subset=['STCK_BSOP_DATE'], keep='last')

    except Exception as e:
        logger.error(f"[{st_code}] 현재가 조회 실패: {e}")
        return

    # 1.3 외국인/기관 순매수 데이터 (Mock)
    net_buying = mock_service.get_net_buying_data(
        st_code,
        float(current_price),
        int(today_data.get("ACML_VOL", 0))
    )

    frgn_ntby_qty = net_buying.get("frgn_ntby_qty", 0)
    pgtr_ntby_qty = net_buying.get("pgtr_ntby_qty", 0)

    # === 2. 전략 분석 ===

    # 진입가 계산 (SIGNAL이 1 또는 2인 경우)
    entry_price = None
    if current_signal in (1, 2):
        # 실제로는 TRADE_HISTORY에서 매수가 조회해야 함
        # 간단히 INIT_AMOUNT / CUR_AMOUNT 기반 추정
        # TODO: TRADE_HISTORY 테이블 연동
        entry_price = Decimal(str(swing.INIT_AMOUNT))  # 임시: 초기 투자금을 진입가로 가정

    # TYPE별 전략 실행
    if swing_type == 'A':
        # TYPE A: 안정적 진입 전략
        analysis = TypeAStrategy.analyze(
            df=df,
            current_price=current_price,
            frgn_ntby_qty=frgn_ntby_qty,
            pgtr_ntby_qty=pgtr_ntby_qty,
            entry_price=entry_price,
            current_signal=current_signal
        )
    elif swing_type == 'B':
        # TYPE B: 모멘텀 추격 전략
        analysis = TypeBStrategy.analyze(
            df=df,
            current_price=current_price,
            entry_price=entry_price,
            current_signal=current_signal
        )
    else:
        logger.warning(f"[{st_code}] 지원하지 않는 SWING_TYPE: {swing_type}")
        return

    # === 3. 신호 처리 (Signal-only mode, no execution) ===

    signal = analysis.get("signal")
    strength = analysis.get("strength")
    reason = analysis.get("reason")

    logger.info(
        f"[{st_code}] 분석 결과: {signal.upper()} ({strength or 'N/A'}) - {reason}"
    )

    # State Transition Logic
    new_signal = current_signal

    if signal == "buy":
        if current_signal == 0:
            # 0 → 1: 첫 매수 신호
            new_signal = 1
            logger.info(f"[{st_code}] 신호 전환: 0 → 1 (첫 매수 신호)")
        elif current_signal == 1 and strength == "strong":
            # 1 → 2: 추가 매수 신호 (strong buy만)
            new_signal = 2
            logger.info(f"[{st_code}] 신호 전환: 1 → 2 (추가 매수 신호)")

    elif signal == "sell":
        if current_signal in (1, 2):
            # 1/2 → 3: 매도 신호
            new_signal = 3
            logger.info(f"[{st_code}] 신호 전환: {current_signal} → 3 (매도 신호)")

            # 매도 완료 후 일정 시간 후 초기화 (다음 배치에서 처리)
            # 여기서는 3으로만 전환, 실제 초기화는 별도 로직 또는 수동 처리

    # === 4. Database Update ===

    if new_signal != current_signal:
        try:
            await swing_service.update_swing(
                swing_id,
                {
                    "SIGNAL": new_signal,
                    "MOD_DT": datetime.now()
                }
            )
            await db.commit()
            logger.info(f"[{st_code}] SIGNAL 업데이트 완료: {current_signal} → {new_signal}")
        except Exception as e:
            await db.rollback()
            logger.error(f"[{st_code}] SIGNAL 업데이트 실패: {e}", exc_info=True)

    # === 5. 로깅 (신호 기록) ===

    # TODO: 신호 이력 테이블에 저장 (선택 사항)
    # SIGNAL_HISTORY 테이블 생성하여 모든 신호 기록

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
