from main import app
from services.SwingService import get_all_swing


async def job():
    swing_list = await get_all_swing(app.state.db_pool)
    for swing in swing_list:
        print("#################스윙 시작################")
        print(swing.SWING_ID, swing.STOCK_CODE, swing.APP_KEY, swing.SECRET_KEY)

    print("#################스윙 끝################")
