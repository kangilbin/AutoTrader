from datetime import timedelta

from fastapi import FastAPI, Response, Request, Depends, WebSocket, HTTPException
from api.KISOpenApi import oauth_token
from api.LocalStockApi import get_stock_balance
from depends.Header import session_token
from model import SignupModel
from module.DBConnection import DBConnectionPool
from module.RedisConnection import redis_pool, redis
import json
from typing import Dict
from contextlib import asynccontextmanager
from queries.KIS_LOCAL_STOCKS import get_stocks
from queries.ACCOUNT import user_signup, user_login
from services.middleware import JWTAuthMiddleware
from fastapi_jwt_auth import AuthJWT
from model.JwtModel import Settings

# 클라이언트 WebSocket 연결을 관리하는 딕셔너리
connected_clients: Dict[str, WebSocket] = {}


# 의존성 주입을 위한 설정
@AuthJWT.load_config
def get_config():
    return Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = DBConnectionPool(max_size=10)
    app.state.redis_pool = await redis_pool(max_size=10)
    try:
        yield
    finally:
        while app.state.db_pool.pool:
            conn = app.state.db_pool.pool.pop()
            try:
                await conn.close()
            except Exception as e:
                print(f"Error closing connection: {e}")

        app.state.redis_pool.close()
        await app.state.redis_pool.wait_closed()

app = FastAPI(lifespan=lifespan)
app.add_middleware(JWTAuthMiddleware)

# 회원 가입
@app.post("/signup")
async def signup(user: SignupModel):
    # 디바이스 정보 검증
    response = await oauth_token(user.API_KEY, user.SECRET_KEY)

    token = response.get("access_token")
    if (not token) or (response.get("error_code")):
        return {"message": response.get("error_description"), "code": response.get("error_code")}

    try:
        await user_signup(app.state.db_pool, user)
    except Exception as e:
        return {"message": "오류인듯", "error": str(e)}

    return {"message": "회원 가입 성공", "code": 200}

# 로그인
@app.post("/login")
async def login(request: Request, response:Response, Authorize: AuthJWT = Depends()):
    data = await request.json()
    user_id = data.get("ID")
    user_pw = data.get("PASSWORD")

    # 사용자 검증
    user = await user_login(app.state.db_pool, user_id, user_pw)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")



    # 액세스 토큰과 리프레시 토큰 발급
    access_token = Authorize.create_access_token(subject=user_id)
    refresh_token = Authorize.create_refresh_token(subject=user_id)

    user_info = json.loads(user)
    user_info.put("refresh_token", refresh_token)

    await redis().set(id, json.dumps(user_info), ex=timedelta(days=7))
    response.set_cookie(key="access_token", value=access_token, httponly=True)
    response.set_cookie(key="refresh_token", value=refresh_token, httponly=True)

    return {"message": "로그인 성공"}


# 리프레시 토큰을 이용해 새로운 액세스 토큰 발급
@app.post("/refresh")
async def refresh(request: Request, Authorize: AuthJWT = Depends()):
    # Authorization 헤더에서 리프레시 토큰 추출
    refresh_token = request.cookies.get("refresh_token")

    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token missing")

    # 액세스 토큰에서 사용자 ID 추출
    user_id = Authorize.get_jwt_subject()

    # 리프레시 토큰이 저장되어 있는지 확인
    user_info = json.loads(await redis().get(user_id))
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if not (user_info.get("refresh_token") and user_info.get("refresh_token") == refresh_token):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # 리프레시 토큰을 사용해 새로운 액세스 토큰 발급
    access_token = Authorize.create_access_token(subject=user_id)

    return {"access_token": access_token}

# 로그아웃
@app.post("/logout")
async def logout(request: Request, response: Response, Authorize: AuthJWT = Depends()):
    # 쿠키에서 리프레시 토큰 가져오기
    access_token = request.cookies.get("access_token")

    if not access_token:
        raise HTTPException(status_code=401, detail="Access token not found")

    user_id = Authorize.get_jwt_subject()

    # 토큰 제거
    response.delete_cookie("refresh_token")
    response.delete_cookie("access_token")
    await redis().delete(user_id)

    return {"message": "로그아웃 성공"}

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

