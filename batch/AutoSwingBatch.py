from module.AESCrypto import decrypt
from module.FetchAPI import fetch
from module.RedisConnection import get_redis
from services.SwingService import get_all_swing
import asyncio


async def trade_job():
    # db = await get_redis()
    # swing_list = await get_all_swing(db)
    # for swing in swing_list:
    #     print("#################스윙 시작################")
    #     print(swing.SWING_ID, swing.STOCK_CODE, decrypt(swing.APP_KEY), decrypt(swing.SECRET_KEY))

    #  비동기 작업을 백그라운드에서 실행
    # task = asyncio.create_task(fetch("GET", api_url, body={}, headers=headers))
    # tasks.append(task)
    # await asyncio.gather(*tasks) #  모든 작업이 완료될 때까지 기다리는 함수
    print("#################1분 스윙################")


# 일 데이터 수집 (고가, 저가, 종가, 거래량)
async def day_collect_job():
    print("#################데이터 수집 시작################")
    print("#################데이터 수집 끝################")