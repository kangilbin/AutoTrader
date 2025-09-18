from sqlalchemy.ext.asyncio import AsyncSession

from app.batch.tech_analysis import sell_or_buy
from app.services.StockService import get_stock_info
from app.crud.StockCrud import update_stock
from app.crud.SwingCrud import insert_swing, select_swing, select_swing_account, list_day_swing, update_swing, delete_swing
from app.model.schemas.SwingModel import SwingCreate
from app.batch.StockDataBatch import fetch_and_store_3_years_data, get_batch_status as get_stock_batch_status
from datetime import datetime, UTC
from app.services.StockService import get_day_stock_price
from datetime import date
import pandas as pd
import asyncio


# 스윙 전략 등록
async def create_swing(db: AsyncSession, swing_data: SwingCreate):
    # 스윙 등록
    await insert_swing(db, swing_data)

    stock_data = await get_stock_info(db, swing_data.ST_CODE)

    # 데이터 적재 여부
    if stock_data["DATA_YN"] != 'Y':
        # 3년 데이터 적재를 백그라운드에서 실행 (기존 DB 세션 전달)
        asyncio.create_task(fetch_and_store_3_years_data(swing_data.USER_ID, swing_data.ST_CODE, stock_data))

        # 스톡 데이터 상태를 즉시 업데이트 (백그라운드 작업 시작을 표시)
        stock_data["MOD_DT"] = datetime.now(UTC)
        await update_stock(db, stock_data)


# 스윙 전략 수정
async def mod_swing(db: AsyncSession, swing_data: SwingCreate):
    swing_data.MOD_DT = datetime.now(UTC)
    return await update_swing(db, swing_data)


# 스윙 전략 조회
async def get_swing(db: AsyncSession, swing_id: int):
    return await select_swing(db, swing_id)


# 스윙 전략 조회(계좌 번호)
async def get_swing_account_no(db: AsyncSession, user_id: str, account_no: str):
    return await select_swing_account(db, user_id, account_no)


# 모든 등록된 스윙 조회
async def get_day_swing(db: AsyncSession):
    return await list_day_swing(db)


# 스윙 전략 삭제
async def remove_swing(db: AsyncSession, swing_id: int):
    return await delete_swing(db, swing_id)


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
    return await get_stock_batch_status(db, code)


# 백 테스팅
async def backtest_swing(db: AsyncSession, swing_data: SwingCreate):
    """
    스윙 전략 백테스팅 (현재 기준 1년 전까지)
    AutoSwingBatch의 실제 매매 로직을 사용하여 백테스팅 실행
    
    Args:
        db: 데이터베이스 세션
        swing_data: 스윙 전략 데이터
    
    Returns:
        dict: 백테스팅 결과
    """

    
    try:
        # 필수 필드 검증
        if not swing_data.ST_CODE:
            return {
                "success": False,
                "message": "주식 코드(ST_CODE)는 필수입니다.",
                "error": "ST_CODE가 누락되었습니다."
            }
        
        # 기본값 설정
        short_term = swing_data.SHORT_TERM if swing_data.SHORT_TERM else 5
        medium_term = swing_data.MEDIUM_TERM if swing_data.MEDIUM_TERM else 20
        long_term = swing_data.LONG_TERM if swing_data.LONG_TERM else 60
        initial_capital = swing_data.SWING_AMOUNT if swing_data.SWING_AMOUNT else 10000000
        
        # 현재 날짜 기준으로 1년 전까지 설정
        end_date = date.today()
        start_date = date(end_date.year - 1, end_date.month, end_date.day)
        
        # 1년치 주가 데이터 조회 (365일)
        price_days = await get_day_stock_price(db, swing_data.ST_CODE, 365)
        
        if not price_days:
            return {
                "success": False,
                "message": "주가 데이터가 없습니다.",
                "error": "데이터가 비어있습니다."
            }
        
        # DataFrame으로 변환
        df = pd.DataFrame([price_day.__dict__ for price_day in price_days])
        df = df.drop(columns=["_sa_instance_state"], errors="ignore")
        
        # 컬럼명을 AutoSwingBatch 형식으로 변환
        if 'CLOSE_PRICE' in df.columns:
            df['STCK_CLPR'] = df['CLOSE_PRICE']
        if 'HIGH_PRICE' in df.columns:
            df['STCK_HGPR'] = df['HIGH_PRICE']
        if 'LOW_PRICE' in df.columns:
            df['STCK_LWPR'] = df['LOW_PRICE']
        if 'TRADE_QTY' in df.columns:
            df['ACML_VOL'] = df['TRADE_QTY']
        
        # 백테스팅 실행
        current_capital = initial_capital
        trades = []
        portfolio_values = []
        
        # 날짜별로 순회하며 백테스팅 실행
        for i in range(len(df)):
            if i < long_term:  # 충분한 데이터가 있을 때까지 스킵
                continue
                
            current_data = df.iloc[:i+1]
            
            # AutoSwingBatch의 sell_or_buy 함수 사용
            first_buy_signal, second_buy_signal, first_sell_signal, second_sell_signal, stop_loss_signal = sell_or_buy(
                current_data, 
                short_term, 
                medium_term, 
                long_term, 
                current_capital, 
                0.05
            )
            
            current_price = current_data['STCK_CLPR'].iloc[-1]
            current_date = current_data.index[-1] if hasattr(current_data.index, 'iloc') else i
            
            # 매수 신호 처리
            if first_buy_signal and current_capital > 0:
                buy_amount = current_capital * 0.5  # 50% 매수
                buy_quantity = int(buy_amount / current_price)
                if buy_quantity > 0:
                    current_capital -= buy_amount
                    trades.append({
                        'date': current_date,
                        'action': 'BUY',
                        'quantity': buy_quantity,
                        'price': current_price,
                        'amount': buy_amount,
                        'reason': '1차 매수 신호'
                    })
            
            if second_buy_signal and current_capital > 0:
                buy_amount = current_capital * 0.5  # 50% 매수
                buy_quantity = int(buy_amount / current_price)
                if buy_quantity > 0:
                    current_capital -= buy_amount
                    trades.append({
                        'date': current_date,
                        'action': 'BUY',
                        'quantity': buy_quantity,
                        'price': current_price,
                        'amount': buy_amount,
                        'reason': '2차 매수 신호'
                    })
            
            # 매도 신호 처리 (간단한 구현)
            if first_sell_signal or second_sell_signal or stop_loss_signal:
                # 모든 보유 주식을 매도한다고 가정
                total_quantity = sum([trade['quantity'] for trade in trades if trade['action'] == 'BUY'])
                if total_quantity > 0:
                    sell_amount = total_quantity * current_price
                    current_capital += sell_amount
                    trades.append({
                        'date': current_date,
                        'action': 'SELL',
                        'quantity': total_quantity,
                        'price': current_price,
                        'amount': sell_amount,
                        'reason': '매도 신호' if not stop_loss_signal else '손절매'
                    })
            
            # 포트폴리오 가치 기록
            portfolio_value = current_capital
            portfolio_values.append({
                'date': current_date,
                'value': portfolio_value,
                'cash': current_capital
            })
        
        # 결과 계산
        final_capital = current_capital
        total_return = (final_capital - initial_capital) / initial_capital if initial_capital > 0 else 0
        
        return {
            "success": True,
            "message": "백테스팅이 성공적으로 완료되었습니다.",
            "data": {
                "strategy_name": "스윙 전략 (AutoSwingBatch 로직)",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "initial_capital": initial_capital,
                "final_capital": final_capital,
                "total_return": total_return,
                "total_trades": len(trades),
                "parameters": {
                    "ST_CODE": swing_data.ST_CODE,
                    "SHORT_TERM": short_term,
                    "MEDIUM_TERM": medium_term,
                    "LONG_TERM": long_term
                },
                "trades": trades,
                "portfolio_values": portfolio_values
            }
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"백테스팅 실행 중 오류 발생: {str(e)}",
            "error": str(e)
        }

