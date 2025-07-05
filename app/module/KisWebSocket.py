import json
import logging
import asyncio
from fastapi import WebSocket
from app.api.KISOpenApi import get_approval
from app.module.RedisConnection import get_redis
from app.module.JwtUtils import verify_token
import websockets
from fastapi import WebSocketDisconnect


async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    try:
        # 첫 메시지로 인증 토큰 받기
        auth_message = await websocket.receive_json()
        
        if auth_message.get("type") != "auth" or not auth_message.get("token"):
            await websocket.close(code=401, reason="인증 토큰이 필요합니다")
            return
        
        # 토큰 검증
        token_data = verify_token(auth_message.get("token"))
        if not token_data:
            await websocket.close(code=401, reason="유효하지 않은 토큰입니다")
            return
        
        user_id = token_data.user_id
        redis = await get_redis()

        # API 서버 연결 정보 가져오기
        socket_data = await redis.hgetall(f"{user_id}_socket_token")
        if not socket_data or not socket_data.get("url") or not socket_data.get("socket_token"):
            socket_data = await get_approval(user_id)

        api_websocket_url = socket_data.get("url")
        socket_token = socket_data.get("socket_token")

        # API 서버와 연결
        api_websocket = await websockets.connect(api_websocket_url)
        
        # 연결 성공 메시지 전송
        await websocket.send_json({
            "type": "connection",
            "status": "connected",
            "message": "API 서버와 연결되었습니다."
        })

        # 두 개의 태스크를 동시에 실행: 클라이언트 ↔ API 서버 중계
        client_to_api_task = asyncio.create_task(
            forward_client_to_api(websocket, api_websocket, socket_token)
        )
        api_to_client_task = asyncio.create_task(
            forward_api_to_client(websocket, api_websocket)
        )

        # 두 태스크 중 하나라도 완료되면 종료
        pending = await asyncio.wait(
            [client_to_api_task, api_to_client_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        # 남은 태스크들 취소
        for task in pending:
            task.cancel()

    except Exception as e:
        logging.error(f"WebSocket Error: {e}")
        await websocket.close(code=1000, reason=str(e))
    finally:
        if 'api_websocket' in locals():
            await api_websocket.close()


async def forward_client_to_api(client_websocket: WebSocket, api_websocket, socket_token: str):
    """클라이언트에서 API 서버로 메시지 전달"""
    message_count = 0
    try:
        while True:
            message_count += 1
            # 클라이언트 메시지 수신
            logging.info(f"=== 메시지 #{message_count} 수신 대기 중 ===")
            data = await client_websocket.receive_json()
            logging.info(f"=== 메시지 #{message_count} 수신 완료: {data} ===")
            
            # 데이터 검증
            if not validate_message_data(data):
                logging.warning(f"=== 메시지 #{message_count} 유효하지 않음: {data} ===")
                continue
            
            # API 서버로 메시지 전달
            formatted_message = send_message(data, socket_token)
            logging.info(f"=== 메시지 #{message_count} API 서버 전송 시작: {formatted_message[:100]}... ===")
            await api_websocket.send(formatted_message)
            logging.info(f"=== 메시지 #{message_count} API 서버 전송 완료 ===")
            
    except WebSocketDisconnect:
        # 정상적인 연결 종료
        logging.info("클라이언트 웹소켓 연결이 종료되었습니다.")
        raise
    except Exception as e:
        logging.error(f"Client to API forwarding error: {e}")
        logging.error(f"API websocket state: {api_websocket.state}")
        raise


async def forward_api_to_client(client_websocket: WebSocket, api_websocket):
    """API 서버에서 클라이언트로 메시지 전달"""
    try:
        while True:
            # API 서버 응답 수신
            response = await api_websocket.recv()
            
            # 클라이언트로 전달
            await client_websocket.send_text(response)
            
    except WebSocketDisconnect:
        # 정상적인 연결 종료
        logging.info("클라이언트 웹소켓 연결이 종료되었습니다.")
        raise
    except Exception as e:
        logging.error(f"API to Client forwarding error: {e}")
        logging.error(f"API websocket state: {api_websocket.state}")
        raise


def validate_message_data(data: dict) -> bool:
    """클라이언트 메시지 데이터 검증"""
    required_fields = ['tr_type', 'tr_id', 'st_code']
    
    for field in required_fields:
        if field not in data:
            logging.error(f"필수 필드 누락: {field}")
            return False
        
        if not data[field] or data[field] == "" or data[field] is None:
            logging.error(f"필드 값이 비어있음: {field} = {data[field]}")
            return False
    
    return True


# 클라이언트 메시지 포맷
def send_message(data: dict, socket_token: str):
    custtype = 'P'    # 고객구분, P: 개인, I: 기관
    tr_type = data['tr_type']
    tr_id = data['tr_id']
    stockcode = data['st_code']
    
    # 추가 검증
    if not all([tr_type, tr_id, stockcode]):
        raise ValueError("필수 필드가 비어있습니다")
    
    senddata = '{"header":{"approval_key":"' + socket_token + '","custtype":"' + custtype + '","tr_type":"' + tr_type + '","content-type":"utf-8"},"body":{"input":{"tr_id":"' + tr_id + '","tr_key":"' + stockcode + '"}}}'
    
    return senddata
