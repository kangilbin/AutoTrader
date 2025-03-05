from fastapi import FastAPI, Response, Depends, WebSocket

from api.KISOpenApi import oauth_token
from api.LocalStockApi import get_stock_balance
from depends.Header import session_token
from model import SignupModel
from model.RequestModel import Account
from module.DBConnection import DBConnectionPool
from module.RedisClient import redis_client
import uuid
import json
from typing import Dict
from contextlib import asynccontextmanager
from queries.KIS_LOCAL_STOCKS import get_stocks
from queries.ACCOUNT import user_signup


# 클라이언트 WebSocket 연결을 관리하는 딕셔너리
connected_clients: Dict[str, WebSocket] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = DBConnectionPool(max_size=10)
    try:
        yield
    finally:
        while app.state.db_pool.pool:
            conn = app.state.db_pool.pool.pop()
            try:
                await conn.close()
            except Exception as e:
                print(f"Error closing connection: {e}")

app = FastAPI(lifespan=lifespan)


# 회원 가입
@app.post("/signup")
async def signup(user: SignupModel):
    # 디바이스 정보 검증
    response = await oauth_token(user.API_KEY, user.SECRET_KEY)

    token = response.get("access_token")
    if (not token) or (response.get("error_code")):
        return {"message": response.get("error_description"), "code": response.get("error_code")}

    try:
        sql = await user_signup(app.state.db_pool, user)
    except Exception as e:
        return {"message": sql, "error": str(e)}

    return {"message": "회원 가입 성공", "token": token}

# 로그인
@app.post("/login")
async def login(request: Account, response:Response):
    redis = await redis_client()

    account_info = {"CANO": request.CANO, "ACNT_PRDT_CD": request.ACNT_PRDT_CD}
    # 세션 ID 발급
    session_id = str(uuid.uuid4())

    await redis.set(session_id, json.dumps(account_info), ex=3600)
    response.set_cookie(key="session_id", value=session_id, httponly=True)
    return {"message": "로그인 완료"}


# 잔고 조회
@app.get("/balance")
async def stock_balance(session_data: dict = Depends(session_token)):
    balance = await get_stock_balance(session_data["CANO"], session_data["ACNT_PRDT_CD"])
    return {"message": "계좌 잔고 조회", "balance": balance}


# 종목 코드 조회
@app.get("/stock")
async def stock(name: str):
    stock_info = await get_stocks(app.state.db_pool, name)
    return {"message": "종목 코드 조회", "stock": stock_info}

# 주식 현재가/호가
# @app.get("/socket")
# async def websocket_endpoint(session_data: dict = Depends(session_token)):
#     await websocket.accept()
#     connected_clients[client_id] = websocket  # 연결된 클라이언트 저장
#
#     try:
#         while True:
#             # 클라이언트 메시지 대기 (필수는 아님)
#             data = await websocket.receive_text()
#             print(f"클라이언트 {client_id} 메시지: {data}")
#     except Exception as e:
#         print(f"클라이언트 {client_id} 연결 종료: {e}")
#         del connected_clients[client_id]  # 연결 종료 시 삭제

# 보유 주식



# 주식 매매


# 주식 매도

