import httpx

from module.Config import get_env
from module.FetchAPI import fetch
from module.RedisClient import redis_client


# 한국 투자증권 접근 토큰
# 유효기간 24시 이며 (1일 1회 발급) 갱신발급 주기는 6시간(6시 이내는 기존 발급키 응답)
# grant_type : 권한
# appkey : 앱키
# appsecret : 앱 시크키
async def oauth_token():
    redis = await redis_client()
    access_token = await redis.get("access_token")

    # 값이 있으면 반환
    if access_token:
        return {"access_token": access_token}

    path = "oauth2/tokenP"
    api_url = f"{get_env('API_URL')}/{path}"

    body = {
        "grant_type": "client_credentials",
        "appkey": get_env("API_KEY"),
        "appsecret": get_env("SECRET_KEY")
    }

    response_data = await fetch("POST", api_url, json=body)

    # Redis에 토큰 저장 만료기간(expires_in) 설정
    access_token = response_data.get("access_token")
    await redis.set("access_token", access_token, ex=response_data.get("expires_in"))


# 자꾸 오류남 토큰 호출하면

# 해쉬키(Hashkey)는 보안을 위한 요소
# 해쉬키를 사용하면 POST로 보내는 요청(주로 주문/정정/취소 API 해당)의 body 값을 사전에 암호화시킬 수 있다.
# ex)
# datas = {
# "CANO": '00000000',
# "ACNT_PRDT_CD": "01",
# "OVRS_EXCG_CD": "SHAA"
# }
async def hash_key(datas):
    path = "uapi/hashkey"
    api_url = f"{get_env('API_URL')}/{path}"
    headers = {
        "appkey": get_env("API_KEY"),
        "appsecret	": get_env("SECRET_KEY")
    }

    response_data = await fetch("POST",api_url, body=datas, headers=headers)

    return response_data.json()["HASH"]
