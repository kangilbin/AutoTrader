import json
import logging
from fastapi import WebSocket
from app.api.KISOpenApi import get_approval
from app.module.RedisConnection import get_redis
from app.module.JwtUtils import verify_token
import websockets

connected_clients = {}


async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    try:
        # 첫 메시지로 인증 토큰 받기
        auth_message = await websocket.receive_json()
        
        if auth_message.get("type") != "auth" or not auth_message.get("token"):
            await websocket.close(code=401, reason="인증 토큰이 필요합니다")
            return
        
        # 토큰 검증
        token_data = verify_token(auth_message["token"])
        if not token_data:
            await websocket.close(code=401, reason="유효하지 않은 토큰입니다")
            return
        
        user_id = token_data.user_id
        redis = await get_redis()

        connected_clients[user_id] = websocket  # 연결된 클라이언트 저장

        socket_data = await redis.hgetall(f"{user_id}_socket_token")
        if not socket_data or not socket_data.get("url") or not socket_data.get("socket_token"):
            socket_data = await get_approval(user_id)

        api_websocket_url = socket_data.get("url")
        socket_token = socket_data.get("socket_token")

        async with websockets.connect(api_websocket_url) as api_websocket:
            while True:
                # 클라이언트 메시지 수신
                data = await websocket.receive_json()

                # API 서버로 메시지 전달
                await api_websocket.send(send_message(data, socket_token))

                # API 응답 수신 후 클라이언트로 전달
                response = await api_websocket.recv()
                await websocket.send_text(response)
    except Exception as e:
        logging.error(f"Error: {e}")
        await websocket.close(code=4001, reason=str(e))
    finally:
        if 'user_id' in locals():
            connected_clients.pop(user_id, None)


# 클라이언트 메시지 포맷
def send_message(data: dict,  socket_token: str):
    # 주식 호가
    # tr_id = 'H0STASP0'
    # tr_type = '1'
    stockcode = '005930'    # 테스트용 임시 종목 설정, 삼성전자
    custtype = 'P'    # 고객구분, P: 개인, I: 기관
    senddata = json.dumps({
        "header": {
            "approval_key": socket_token,
            "custtype": custtype,
            "tr_type": data['tr_type'],
            "content-type": "utf-8"
        },
        "body": {
            "input": {
                "tr_id": data['tr_id'],
                "tr_key": stockcode
            }
        }
    })

    return senddata
