import asyncio
import logging
import pandas as pd
from app.api.local_stock_api import get_target_price
from app.batch.tech_analysis import sell_or_buy
from app.crud.stock_crud import insert_bulk_stock_hstr
from app.module.aes_crypto import decrypt
from app.module.db_connection import get_db
from app.services.stock_service import get_day_stock_price
from app.services.swing_service import get_day_swing

async def trade_job():
    db = await get_db()
    swing_list = await get_day_swing(db)

    tasks = []
    for swing in swing_list:
        print("#################스윙 시작################")
        print(swing.SWING_ID, swing.STOCK_CODE, decrypt(swing.APP_KEY), decrypt(swing.SECRET_KEY))

        # 1. 이평선 조회
        price_days = await get_day_stock_price(db, swing.ST_CODE, swing.LONG_TERM)
        df = pd.DataFrame([price_day.__dict__ for price_day in price_days])
        df = df.drop(columns=["_sa_instance_state"], errors="ignore")
        new_row = pd.DataFrame([["01231230","2025-08-03", 15000, 17000, 13000, 14000, 200000]], columns=["ST_CODE", "STCK_BSOP_DATE", "STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR", "ACML_VOL"])
        df = pd.concat([df, new_row], ignore_index=True)

        first_buy_signal, second_buy_signal, first_sell_signal, second_sell_signal, stop_loss_signal = sell_or_buy(df, swing.SHORT_TERM, swing.MEDIUM_TERM, swing.LONG_TERM, swing.SWING_AMOUNT, swing.RSI_PERIOD,0.05)

        if stop_loss_signal:
            # 손절 신호 발생
            print("손절 신호 발생")
            # 매도 로직 실행
            # swing.SIGNAL = "0" 초기화
        else:
            if swing.SIGNAL == "0": # 최초 상태
                # 단기-중기 매수 신호 발생
                if first_buy_signal:
                    # 매수 신호 발생
                    print("단기-중기 매수 신호 발생")
                    # 매수 로직 실행
                    # swing.SIGNAL = "1"
            elif swing.SIGNAL == "1": # 첫 매수 후
                if second_buy_signal:
                    # 매수 신호 발생
                    print("중기-장기 매수 신호 발생")
                    # 매수 로직 실행
                    # swing.SIGNAL = "2"
                elif first_sell_signal | second_sell_signal:
                    # 매도 신호 발생
                    print("단기-중기 매도 신호 발생")
                    # 매도 로직 실행
                    # 첫 매수 후 매도 신호 발생하면 전량 매도
                    # swing.SIGNAL = "0" 초기화
            elif swing.SIGNAL == "2": # 두 번째 매수 후
                if first_sell_signal:
                    # 매도 신호 발생
                    print("단기-중기 매도 신호 발생")
                    # 매도 로직 실행
                    # 두 번째 매수 후 매도 신호 발생하면 전량 매도
                    # swing.SIGNAL = "3"
                elif second_sell_signal:
                    # 매도 신호 발생
                    print("중기-장기 매도 신호 발생")
                    # 매도 로직 실행
                    # 발생하면 전량 매도
                    # swing.SIGNAL = "0" 초기화
            elif swing.SIGNAL == "3": # 두 번째 매도 후
                if second_sell_signal:
                    # 매도 신호 발생
                    print("중기-장기 매도 신호 발생")
                    # 매도 로직 실행
                    # 발생하면 전량 매도
                    # swing.SIGNAL = "0" 초기화


        # 3. 보조 지표 (ADX, OBV) 필터링

        # 매수 or 매매 실행

        # 비동기 작업을 백그라운드에서 실행
        #task = asyncio.create_task(fetch("GET", api_url, body={}, headers=headers))
        #tasks.append(task)
        #await asyncio.gather(*tasks) #  모든 작업이 완료될 때까지 기다리는 함수
        print("#################1시간 스윙################")


async def day_collect_job():
    """
    일 데이터 수집
    stck_hgpr : 고가
    stck_lwpr : 저가
    stck_clspr : 종가
    acml_vol : 거래량
    """
    logging.debug("#################데이터 수집 시작################")
    db = await get_db()
    swing_stock_list = await get_day_swing(db)

    tasks = []
    for swing_stock in swing_stock_list:
        code = swing_stock.STOCK_CODE
        tasks.append(collect_and_insert_stock_data(db, code))

    await asyncio.gather(*tasks)
    logging.debug("#################데이터 수집 종료################")


async def collect_and_insert_stock_data(db, code):
    response = await get_target_price(code)
    response.set("STOCK_CODE", code)
    await insert_bulk_stock_hstr(db, response)



