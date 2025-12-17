"""
주식 데이터 배치 작업 - 3년치 데이터 적재
"""
from datetime import datetime
from dateutil.relativedelta import relativedelta
import asyncio
import logging
import time

from app.external.kis_api import get_stock_data
from app.common.database import Database
from app.domain.stock.service import StockService

logger = logging.getLogger(__name__)

MAX_ITEMS_PER_REQUEST = 100


async def fetch_and_store_3_years_data(user_id: str, code: str, stock_data: dict):
    """
    3년치 주식 데이터를 백그라운드에서 병렬로 가져와서 DB에 저장하는 배치 작업

    Args:
        user_id: 사용자 ID
        code: 주식 코드
        stock_data: 주식 메타 정보
    """
    db = await Database.get_session()
    try:
        stock_service = StockService(db)

        # 상태 업데이트: 처리 중
        await stock_service.update_stock(code, {"DATA_YN": 'P'})
        logger.info(f"Started background data fetch for {code}")

        end_date = datetime.now().date()
        start_date = end_date - relativedelta(years=3)
        current_date = start_date

        # 날짜 범위 생성
        date_ranges = []
        while current_date < end_date:
            next_date = current_date + relativedelta(days=MAX_ITEMS_PER_REQUEST)
            if next_date > end_date:
                next_date = end_date
            date_ranges.append((current_date, next_date))
            current_date = next_date

        logger.info(f"Created {len(date_ranges)} date ranges for {code}")

        # 동시 실행 제한 세마포어
        semaphore = asyncio.Semaphore(3)

        async def process_date_range(range_start, range_end):
            task_start_time = time.time()
            async with semaphore:
                try:
                    response = await get_stock_data(
                        user_id, code,
                        range_start.strftime('%Y%m%d'),
                        range_end.strftime('%Y%m%d')
                    )
                    if response and "output2" in response:
                        api_data_count = len(response["output2"])
                        task_time = time.time() - task_start_time
                        logger.debug(f"API call completed for {range_start} to {range_end}: {api_data_count} records in {task_time:.2f}s")
                        return response["output2"]
                    else:
                        logger.warning(f"No data for {range_start} to {range_end}")
                        return None
                except Exception as e:
                    logger.error(f"Error processing {range_start} to {range_end}: {e}")
                    return None

        logger.info(f"Processing all {len(date_ranges)} tasks in parallel")

        # 모든 태스크 실행
        tasks = [process_date_range(start, end) for start, end in date_ranges]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 결과 정리 및 DB 저장
        successful_tasks = 0
        failed_tasks = 0
        total_cnt = 0

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                failed_tasks += 1
                logger.error(f"Task failed: {result}")
            elif result is not None:
                try:
                    # history_data 변환
                    history_data = []
                    for item in result:
                        history_data.append({
                            "ST_CODE": code,
                            "STCK_BSOP_DATE": item.get("STCK_BSOP_DATE"),
                            "STCK_OPRC": item.get("STCK_OPRC"),
                            "STCK_HGPR": item.get("STCK_HGPR"),
                            "STCK_LWPR": item.get("STCK_LWPR"),
                            "STCK_CLPR": item.get("STCK_CLPR"),
                            "ACML_VOL": item.get("ACML_VOL"),
                            "REG_DT": datetime.now()
                        })

                    if history_data:
                        cnt = await stock_service.save_history_bulk(history_data)
                        successful_tasks += 1
                        total_cnt += cnt
                        logger.debug(f"DB insert completed for batch {i+1}: {cnt} records")
                except Exception as db_error:
                    failed_tasks += 1
                    logger.error(f"DB insert failed for batch {i+1}: {db_error}")
            else:
                failed_tasks += 1

        # 상태 업데이트: 완료
        await stock_service.update_stock(code, {"DATA_YN": 'Y'})
        logger.info(f"Stock {code} updated to DATA_YN=Y, total {total_cnt} records")

    except Exception as e:
        try:
            stock_service = StockService(db)
            await stock_service.update_stock(code, {"DATA_YN": 'E'})
            logger.error(f"Updated {code} to DATA_YN=E due to error: {e}")
        except Exception as update_error:
            logger.error(f"Failed to update error status for {code}: {update_error}")

        logger.error(f"Background fetch failed for {code}: {e}")
        raise
    finally:
        await db.close()


async def get_batch_status(code: str) -> dict:
    """
    배치 작업 상태 조회

    Args:
        code: 종목 코드

    Returns:
        dict: 상태 정보
    """
    db = await Database.get_session()
    try:
        stock_service = StockService(db)
        stock_data = await stock_service.get_stock_info(code)
        return {
            "code": code,
            "status": stock_data.get("DATA_YN"),
            "last_updated": stock_data.get("MOD_DT")
        }
    except Exception as e:
        logger.error(f"Failed to get status for {code}: {e}")
        raise
    finally:
        await db.close()