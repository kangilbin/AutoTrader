from module.FetchAPI import fetch
from module.Config import get_env
from module.RedisClient import redis_client


# 상품 조회
async def get_overseas_product(pdno:str, prdt_type:str):
    path = "/uapi/domestic-stock/v1/quotations/search-info"
    api_url = f"{get_env('API_URL')}/{path}"

    headers = {
        "authorization" : redis_client().get("access_token"),
        "appkey" : get_env("API_KEY"),
        "appsecret	" : get_env("SECRET_KEY"),
        "tr_id" : "CTPF1604R",
        "custtype" : redis_client().get("custtype") # B:법인, P:개인
    }
    return await fetch(api_url, method="GET", body={}, headers=headers)
