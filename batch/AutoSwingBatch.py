from main import app
from module.AESCrypto import decrypt
from services.SwingService import get_all_swing


async def trade_job():
    swing_list = await get_all_swing(app.state.db_pool)
    for swing in swing_list:
        print("#################스윙 시작################")
        print(swing.SWING_ID, swing.STOCK_CODE, decrypt(swing.APP_KEY), decrypt(swing.SECRET_KEY))

    print("#################스윙 끝################")

# 일 데이터 수집 (고가, 저가, 종가, 거래량)