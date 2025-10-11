from sqlalchemy.ext.asyncio import AsyncSession
from app.api.local_stock_api import get_stock_data
from app.stock.stock_crud import update_stock, insert_bulk_stock_hstr
from app.stock.stock_service import get_stock_info
from app.infrastructure.database.db_connection import get_db
from datetime import datetime
from dateutil.relativedelta import relativedelta
import asyncio
import logging
import time

MAX_ITEMS_PER_REQUEST = 100

# 3년 데이터 적재 (백그라운드 작업)
async def fetch_and_store_3_years_data(user_id: str, code: str, stock_data: dict):
    """
    3년치 주식 데이터를 백그라운드에서 병렬로 가져와서 DB에 저장하는 배치 작업
    
    Args:
        user_id: 사용자 ID
        code: 주식 코드
        stock_data: 주식 메타 정보
        db: DB 세션 (호출자에서 전달받음)
    
    Returns:
        None
    """
    async for db in get_db():
        try:
            # 상태 업데이트: 처리 중
            stock_data["DATA_YN"] = 'P'
            stock_data["MOD_DT"] = datetime.now()
            await update_stock(db, stock_data)
            logging.info(f"Started background data fetch for {code}")

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

            logging.info(f"Created {len(date_ranges)} date ranges for {code}")

            # 동시 실행 제한 세마포어
            semaphore = asyncio.Semaphore(3)

            async def process_date_range(range_start, range_end):
                task_start_time = time.time()
                async with semaphore:
                    try:
                        response = await get_stock_data(user_id, code, range_start.strftime('%Y%m%d'), range_end.strftime('%Y%m%d'))
                        if response and "output2" in response:
                            api_data_count = len(response["output2"])
                            task_time = time.time() - task_start_time
                            logging.debug(f"API call completed for {range_start} to {range_end}: {api_data_count} records in {task_time:.2f}s")
                            return response["output2"]  # 데이터만 반환
                        else:
                            logging.warning(f"No data for {range_start} to {range_end}")
                            return None
                    except Exception as e:
                        logging.error(f"Error processing {range_start} to {range_end}: {e}")
                        return None

            logging.info(f"Processing all {len(date_ranges)} tasks in parallel")

            # 모든 태스크 실행
            tasks = [process_date_range(start, end) for start, end in date_ranges]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 결과 정리 및 DB 저장 (기존 세션 재사용)
            successful_tasks = 0
            failed_tasks = 0
            total_cnt = 0

            # 기존 DB 세션으로 순차 저장
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    failed_tasks += 1
                    logging.error(f"Task failed: {result}")
                elif result is not None:
                    try:
                        cnt = await insert_bulk_stock_hstr(db, result, code)
                        successful_tasks += 1
                        total_cnt += cnt
                        logging.debug(f"DB insert completed for batch {i+1}: {cnt} records")
                    except Exception as db_error:
                        failed_tasks += 1
                        logging.error(f"DB insert failed for batch {i+1}: {db_error}")
                else:
                    failed_tasks += 1

            # 상태 업데이트: 완료
            stock_data["DATA_YN"] = 'Y'
            stock_data["MOD_DT"] = datetime.now()
            await update_stock(db, stock_data)
            logging.info(f"Stock {code} updated to DATA_YN=Y")

            break  # 세션 루프 종료

        except Exception as e:
            try:
                if stock_data is not None:
                    stock_data["DATA_YN"] = 'E'
                    stock_data["MOD_DT"] = datetime.now()
                    await update_stock(db, stock_data)
                    logging.error(f"Updated {code} to DATA_YN=E due to error: {e}")
                else:
                    logging.error(f"stock_data is None; cannot update status for {code}")
            except Exception as update_error:
                logging.error(f"Failed to update error status for {code}: {update_error}")

            logging.error(f"Background fetch failed for {code}: {e}")
            raise


# 배치 작업 상태 조회
async def get_batch_status(db: AsyncSession, code: str):
    """
    배치 작업 상태 조회
    
    Args:
        db: DB 세션
        code: 종목 코드
    
    Returns:
        dict: 상태 정보
    """
    try:
        stock_data = await get_stock_info(db, code)
        return {
            "code": code,
            "status": stock_data["DATA_YN"],
            "last_updated": stock_data["MOD_DT"]
        }
    except Exception as e:
        logging.error(f"Failed to get status for {code}: {e}")
        raise
