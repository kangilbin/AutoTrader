from module.AESCrypto import decrypt
from module.RedisConnection import get_redis
from services.SwingService import get_all_swing


async def trade_job():
    # db = await get_redis()
    # swing_list = await get_all_swing(db)
    # for swing in swing_list:
    #     print("#################스윙 시작################")
    #     print(swing.SWING_ID, swing.STOCK_CODE, decrypt(swing.APP_KEY), decrypt(swing.SECRET_KEY))
    print("#################1분 스윙################")


# 일 데이터 수집 (고가, 저가, 종가, 거래량)
async def day_collect_job():
    print("#################데이터 수집 시작################")
    print("#################데이터 수집 끝################")