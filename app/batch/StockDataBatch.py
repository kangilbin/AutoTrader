from sqlalchemy.ext.asyncio import AsyncSession
from app.api.LocalStockApi import get_stock_data
from app.crud.StockCrud import update_stock, insert_bulk_stock_hstr
from app.services.StockService import get_stock_info
from app.module.DBConnection import get_db
from datetime import datetime, UTC
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
        stock_data: 주식 데이터
    
    Returns:
        None (백그라운드 작업)
    """
        # 백그라운드에서 새로운 DB 세션 생성
    async for db in get_db():
        try:
            # 작업 시작 상태 업데이트
            stock_data["DATA_YN"] = 'P'  # Processing 상태
            stock_data["MOD_DT"] = datetime.now(UTC)
            await update_stock(db, stock_data)
            logging.info(f"Started background data fetch for {code}")
            
            start_time = time.time()  # 전체 시작 시간
            total_cnt = 0
            end_date = datetime.now(UTC).date()  # 오늘 날짜
            start_date = end_date - relativedelta(years=3)  # 3년 전 날짜
            current_date = start_date

            logging.info(f"Starting 3-year data fetch for {code} from {start_date} to {end_date}")

            # 날짜 범위들을 미리 생성
            date_ranges = []
            while current_date < end_date:
                next_date = current_date + relativedelta(days=MAX_ITEMS_PER_REQUEST)
                if next_date > end_date:
                    next_date = end_date
                
                date_ranges.append((current_date, next_date))
                current_date = next_date

            logging.info(f"Created {len(date_ranges)} date ranges for processing")

            # 병렬 실행을 위한 세마포어 (API 서버 부하 방지)
            # readexactly() 에러 방지를 위해 동시 실행 수를 줄임
            semaphore = asyncio.Semaphore(5)  # 동시에 5개까지 실행

            async def process_date_range(range_start, range_end):
                """개별 날짜 범위의 데이터를 처리하는 함수"""
                task_start_time = time.time()  # 개별 작업 시작 시간
                async with semaphore:
                    try:
                        response = await get_stock_data(user_id, code, range_start.strftime('%Y%m%d'), range_end.strftime('%Y%m%d'))
                        
                        if response and "output2" in response:
                            api_data_count = len(response["output2"])
                            cnt = await insert_bulk_stock_hstr(db, response["output2"], code)
                            task_time = time.time() - task_start_time
                            logging.debug(f"Processed {range_start} to {range_end}: API={api_data_count}, DB={cnt} records in {task_time:.2f}s")
                            return cnt
                        else:
                            task_time = time.time() - task_start_time
                            logging.warning(f"No data for {range_start} to {range_end} in {task_time:.2f}s")
                            return 0
                    except Exception as e:
                        task_time = time.time() - task_start_time
                        logging.error(f"Error processing {range_start} to {range_end} in {task_time:.2f}s: {e}")
                        return 0

            # 병렬로 모든 날짜 범위 처리
            logging.info(f"Starting parallel processing with {len(date_ranges)} tasks")
            
            # 배치 크기 설정 (한 번에 처리할 태스크 수)
            # readexactly() 에러 방지를 위해 배치 크기를 줄임
            batch_size = 10
            all_results = []
            
            for i in range(0, len(date_ranges), batch_size):
                batch_ranges = date_ranges[i:i + batch_size]
                logging.info(f"Processing batch {i//batch_size + 1}/{(len(date_ranges) + batch_size - 1)//batch_size}: {len(batch_ranges)} tasks")
                
                tasks = [process_date_range(start, end) for start, end in batch_ranges]
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                all_results.extend(batch_results)
            
            results = all_results
            
            # 결과 집계
            successful_tasks = 0
            failed_tasks = 0
            for result in results:
                if isinstance(result, int):
                    total_cnt += result
                    successful_tasks += 1
                else:
                    failed_tasks += 1
                    logging.error(f"Task failed: {result}")

            # 전체 실행 시간 계산
            total_time = time.time() - start_time
            
            logging.info(f"Completed 3-year data fetch for {code}")
            logging.info(f"Total time: {total_time:.2f} seconds ({total_time/60:.2f} minutes)")
            logging.info(f"Successful tasks: {successful_tasks}, Failed tasks: {failed_tasks}")
            logging.info(f"Total records inserted: {total_cnt}")
            logging.info(f"Average time per task: {total_time/len(date_ranges):.2f} seconds")
            logging.info(f"Records per second: {total_cnt/total_time:.2f}")

            # 작업 완료 후 스톡 데이터 상태 업데이트 (기존 데이터 재사용)
            stock_data["DATA_YN"] = 'Y'  # 데이터 적재 완료
            stock_data["MOD_DT"] = datetime.now(UTC)
            await update_stock(db, stock_data)
            logging.info(f"Updated stock data status to DATA_YN=Y for {code}")
            break  # 성공적으로 완료되면 루프 종료
            
        except Exception as e:
            # 작업 실패 시 상태 업데이트 (기존 데이터 재사용)
            try:
                if stock_data is not None:
                    stock_data["DATA_YN"] = 'E'  # Error 상태
                    stock_data["MOD_DT"] = datetime.now(UTC)
                    await update_stock(db, stock_data)
                    logging.error(f"Updated stock data status to DATA_YN=E for {code} due to error: {e}")
                else:
                    # stock_data가 None인 경우 (get_stock_info에서 실패한 경우)
                    logging.error(f"Cannot update error status for {code}: stock_data is None")
            except Exception as update_error:
                logging.error(f"Failed to update error status for {code}: {update_error}")
            
            logging.error(f"Background data fetch failed for {code}: {e}")
            raise


# 배치 작업 상태 조회
async def get_batch_status(db: AsyncSession, code: str):
    """
    배치 작업의 현재 상태를 조회하는 함수
    
    Args:
        db: 데이터베이스 세션
        code: 주식 코드
    
    Returns:
        dict: 배치 작업 상태 정보
    """
    try:
        stock_data = await get_stock_info(db, code)
        return {
            "code": code,
            "status": stock_data["DATA_YN"],
            "last_updated": stock_data["MOD_DT"]
        }
    except Exception as e:
        logging.error(f"Failed to get batch status for {code}: {e}")
        raise 