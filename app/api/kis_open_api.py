from app.module.config import get_env
from app.module.fetch_api import fetch
from app.module.redis_connection import get_redis
from fastapi import HTTPException


async def oauth_token(user_id: str, simulation_yn: str, api_key: str, secret_key: str):
    """
    한국 투자 증권 접근 토큰
    유효기간 24시 이며 (1일 1회 발급) 갱신발급 주기는 6시간(6시 이내는 기존 발급키 응답)
    :param user_id:
    :param simulation_yn: 모의 투자 여부
    :param api_key: 앱키
    :param secret_key: 앱 시크키
    :return:
    """
    redis = await get_redis()
    access_data = await redis.hgetall(f"{user_id}_access_token")
    if access_data:
        if access_data.get("api_key") == api_key and access_data.get("secret_key") == secret_key:
            return access_data
        else:
            await redis.delete(f"{user_id}_access_token")

    path = "oauth2/tokenP"
    if simulation_yn == "Y":
        api_url = get_env("DEV_API_URL")
    else:
        api_url = get_env("REAL_API_URL")

    url = f"{api_url}/{path}"
    body = {
        "grant_type": "client_credentials",
        "appkey": api_key,
        "appsecret": secret_key
    }

    response = await fetch("POST", url, json=body)
    access_token = response.get("access_token")

    if (not access_token) or (response.get("error_code")):
        raise HTTPException(status_code=403, detail=response.get("error_description"))

    data = {
        "access_token": access_token,
        "api_key": api_key,
        "secret_key": secret_key,
        "simulation_yn": simulation_yn
    }
    # Redis에 토큰 저장 만료기간(expires_in) 설정
    await redis.hset(f"{user_id}_access_token", mapping=data)
    await redis.expire(f"{user_id}_access_token", response.get("expires_in"))
    return data


# 실시간 (웹소켓) 접속키 발급
# 접속키의 유효기간은 24시간이지만, 접속키는 세션 연결 시 초기 1회만 사용하기 때문에 접속키 인증 후에는 세션종료되지 않는 이상
# 접속키 신규 발급받지 않으셔도 365일 내내 웹소켓 데이터 수신하실 수 있습니다.
async def get_approval(user_id: str):
    redis = await get_redis()
    user_auth = await redis.hgetall(f"{user_id}_access_token")
    
    if not user_auth:
        raise HTTPException(status_code=401, detail="사용자 인증 정보가 없습니다.")
    
    if user_auth.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
        socket_url = get_env("DEV_SOCKET_URL")
    else:
        url = get_env("REAL_API_URL")
        socket_url = get_env("REAL_SOCKET_URL")

    path = "oauth2/Approval"
    api_url = f"{url}/{path}"

    body = {
        "grant_type": "client_credentials",
        "appkey": user_auth.get('api_key'),
        "secretkey": user_auth.get('secret_key')
    }
    response = await fetch("POST", api_url, json=body)
    if not response.get("approval_key") or response.get("approval_key") is None:
        raise HTTPException(status_code=401, detail=response.get('error_code'))
    
    data = {
        "socket_token": response.get("approval_key"),
        "url": socket_url
    }
    # Redis에 토큰 저장 만료기간(expires_in) 설정
    redis = await get_redis()
    await redis.hset(f"{user_id}_socket_token", mapping=data)
    await redis.expire(f"{user_id}_socket_token", 86400)
    return data


# 자꾸 오류남 토큰 호출하면

# 해쉬키(Hashkey)는 보안을 위한 요소
# 해쉬키를 사용하면 POST로 보내는 요청(주로 주문/정정/취소 API 해당)의 body 값을 사전에 암호화시킬 수 있다.
# ex)
# datas = {
# "CANO": '00000000',
# "ACNT_PRDT_CD": "01",
# "OVRS_EXCG_CD": "SHAA"
# }
# async def connect():
#
#     # 웹 소켓에 접속.( 주석은 koreainvest test server for websocket)
#     ## 시세데이터를 받기위한 데이터를 미리 할당해서 사용한다.
#
#     g_appkey = '홈페이지에서 발급받은 APP Key'
#     g_appsceret = '홈페이지에서 발급받은 APP Secret'
#     stockcode = '005930'    # 테스트용 임시 종목 설정, 삼성전자
#     htsid = '본인 HTS ID'    # 체결통보용 htsid 입력
#     custtype = 'P'          # customer type, 개인:'P' 법인 'B'
#     url = 'ws://ops.koreainvestment.com:21000' #실전투자
#
#     g_approval_key = get_approval(g_appkey, g_appsceret)
#     print("approval_key [%s]" % (g_approval_key))
#
#     async with websockets.connect(url, ping_interval=60) as websocket:
#         print("1.주식호가, 2.주식호가해제, 3.주식체결, 4.주식체결해제, 5.주식체결통보(고객), 6.주식체결통보해제(고객), 7.주식체결통보(모의), 8.주식체결통보해제(모의)")
#     print("Input Command :")
#     cmd = input()
#
#     # 입력값 체크 step
#     if cmd < '0' or cmd > '9':
#         print("> Wrong Input Data", cmd)
#         continue
#     elif cmd == '0':
#         print("Exit!!")
#         break
#
#     # 입력값에 따라 전송 데이터셋 구분 처리
#     if cmd == '1':  # 주식호가 등록
#         tr_id = 'H0STASP0'
#         tr_type = '1'
#     elif cmd == '2':  # 주식호가 등록해제
#         tr_id = 'H0STASP0'
#         tr_type = '2'
#     elif cmd == '3':  # 주식체결 등록
#         tr_id = 'H0STCNT0'
#         tr_type = '1'
#     elif cmd == '4':  # 주식체결 등록해제
#         tr_id = 'H0STCNT0'
#         tr_type = '2'
#     elif cmd == '5':  # 주식체결통보 등록(고객용)
#         tr_id = 'H0STCNI0' # 고객체결통보
#         tr_type = '1'
#     elif cmd == '6':  # 주식체결통보 등록해제(고객용)
#         tr_id = 'H0STCNI0' # 고객체결통보
#         tr_type = '2'
#     elif cmd == '7':  # 주식체결통보 등록(모의)
#         tr_id = 'H0STCNI9'  #테스트용 직원체결통보
#         tr_type = '1'
#     elif cmd == '8':  # 주식체결통보 등록해제(모의)
#         tr_id = 'H0STCNI9'  # 테스트용 직원체결통보
#         tr_type = '2'
#     else:
#         senddata = 'wrong inert data'
#
#     # send json, 체결통보는 tr_key 입력항목이 상이하므로 분리를 한다.
#     if cmd == '5' or cmd == '6' or cmd == '7' or cmd == '8':
#         senddata = '{"header":{"approval_key":"' + g_approval_key + '","custtype":"' + custtype + '","tr_type":"' + tr_type + '","content-type":"utf-8"},"body":{"input":{"tr_id":"' + tr_id + '","tr_key":"' + htsid + '"}}}'
#     else :
#         senddata = '{"header":{"approval_key":"' + g_approval_key + '","custtype":"' + custtype + '","tr_type":"' + tr_type + '","content-type":"utf-8"},"body":{"input":{"tr_id":"' + tr_id + '","tr_key":"' + stockcode + '"}}}'
#
#     print('Input Command is :', senddata)
#
#     await websocket.send(senddata)
#     # 무한히 데이터가 오기만 기다린다.
#     while True:
#         data = await websocket.recv()
#         print("Recev Command is :", data)
#         if data[0] == '0' or data[0] == '1':  # 실시간 데이터일 경우
#             trid = jsonObject["header"]["tr_id"]
#
#             if data[0] == '0':
#                 recvstr = data.split('|')  # 수신데이터가 실데이터 이전은 '|'로 나뉘어져있어 split
#                 trid0 = recvstr[1]
#                 if trid0 == "H0STASP0":  # 주식호가tr 일경우의 처리 단계
#                     print("#### 주식호가 ####")
#                     stockhoka(recvstr[3])
#                     await asyncio.sleep(0.2)
#
#                 elif trid0 == "H0STCNT0":  # 주식체결 데이터 처리
#                     print("#### 주식체결 ####")
#                     data_cnt = int(recvstr[2])  # 체결데이터 개수
#                     stockspurchase(data_cnt, recvstr[3])
#                     await asyncio.sleep(0.2)
#
#             elif data[0] == '1':
#                 recvstr = data.split('|')  # 수신데이터가 실데이터 이전은 '|'로 나뉘어져있어 split
#                 trid0 = recvstr[1]
#                 if trid0 == "H0STCNI0" or trid0 == "H0STCNI9":  # 주실체결 통보 처리
#                     print("#### 주식체결통보 ####")
#                     stocksigningnotice(recvstr[3], aes_key, aes_iv)
#                     await asyncio.sleep(0.2)
#
#             # clearConsole()
#             # break;
#         else:
#             jsonObject = json.loads(data)
#             trid = jsonObject["header"]["tr_id"]
#
#             if trid != "PINGPONG":
#                 rt_cd = jsonObject["body"]["rt_cd"]
#                 if rt_cd == '1':    # 에러일 경우 처리
#                     print("### ERROR RETURN CODE [ %s ] MSG [ %s ]" % (rt_cd, jsonObject["body"]["msg1"]))
#                     break
#                 elif rt_cd == '0':  # 정상일 경우 처리
#                     print("### RETURN CODE [ %s ] MSG [ %s ]" % (rt_cd, jsonObject["body"]["msg1"]))
#                     # 체결통보 처리를 위한 AES256 KEY, IV 처리 단계
#                     if trid == "H0STCNI0" or trid == "H0STCNI9":
#                         aes_key = jsonObject["body"]["output"]["key"]
#                         aes_iv = jsonObject["body"]["output"]["iv"]
#                         print("### TRID [%s] KEY[%s] IV[%s]" % (trid, aes_key, aes_iv))
#
#             elif trid == "PINGPONG":
#                 print("### RECV [PINGPONG] [%s]" % (data))
#                 await websocket.pong(data)
#                 print("### SEND [PINGPONG] [%s]" % (data))

async def hash_key(datas):
    path = "uapi/hashkey"
    api_url = f"{get_env('API_URL')}/{path}"
    headers = {
        "appkey": get_env("API_KEY"),
        "appsecret	": get_env("SECRET_KEY")
    }

    response_data = await fetch("POST",api_url, body=datas, headers=headers)

    return response_data.json()["HASH"]

