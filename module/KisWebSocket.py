import json
import logging
from fastapi import WebSocket
from api.KISOpenApi import get_approval
from module.RedisConnection import get_redis

connected_clients = {}


async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept()
    connected_clients[user_id] = websocket  # 연결된 클라이언트 저장

    try:
        socket_data = await get_redis().hgetall(f"{user_id}_socket_token")
        if not socket_data:
            socket_data = await get_approval(user_id)
        while True:
            # 클라이언트 메시지 대기
            data = await websocket.receive_json()

            # API 서버로 메시지 전달
            async with WebSocket(socket_data.get("url")) as api_websocket:
                await api_websocket.send_text(send_message(data, socket_data.get("socket_token")))
                response = await api_websocket.receive_text()
                await websocket.send_text(response)
    except Exception as e:
        logging.error(f"Error: {e}")
    finally:
        del connected_clients[user_id]


# 클라이언트 메시지 포맷
async def send_message(data: dict,  socket_token: str):
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
