from app.api.KISOpenApi import oauth_token
from app.module.FetchAPI import fetch
from app.module.Config import get_env
from app.module.RedisConnection import get_redis


async def user(user_id: str):
    redis = await get_redis()

    user_info = redis.hgetall(user_id)
    access_token = redis.get(f"{user_id}_access_token")

    if not access_token:
        response = await oauth_token(user_id, user_info.get("API_KEY"), user_info.get("SECRET_KEY"))
        access_token = response.get("access_token")

    return user_info, access_token


# 상품 조회
async def get_overseas_product(user_id: str):
    user_info, access_token = await user(user_id)

    path = "/uapi/domestic-stock/v1/quotations/search-info"
    api_url = f"{get_env('API_URL')}/{path}"

    headers = {
        "authorization" : access_token,
        "appkey" : user_info.get("API_KEY"),
        "appsecret	" : user_info.get("SECRET_KEY"),
        "tr_id" : "CTPF1604R",
        "custtype" : "P" # B:법인, P:개인
    }
    return await fetch("GET", api_url, body={}, headers=headers)

