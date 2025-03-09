from datetime import timedelta

from fastapi import FastAPI, Response, Request, Depends, WebSocket, HTTPException
from api.KISOpenApi import oauth_token
from api.LocalStockApi import get_stock_balance
from model import SignupModel, AccountModel
from module.DBConnection import DBConnectionPool
from module.RedisConnection import redis_pool, redis
import json
from contextlib import asynccontextmanager
from queries.ACCOUNT import account_register, get_account_info, account_delete, get_account_list
from queries.KIS_LOCAL_STOCKS import get_stocks
from queries.USER import user_signup, get_user_info
from services.middleware import JWTAuthMiddleware
from fastapi_jwt_auth import AuthJWT
from model.JwtModel import Settings


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
        return {"message": "오류", "error": str(e)}

    return {"message": "회원 가입 성공", "code": 200}

# 로그인
@app.post("/login")
async def login(request: Request, response:Response, Authorize: AuthJWT = Depends()):
    req = await request.json()
    user_id = req.get("USER_ID")
    user_pw = req.get("PASSWORD")

    # 사용자 검증
    user_info = json.loads(await get_user_info(app.state.db_pool, user_id, user_pw))
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid credentials")



    # 액세스 토큰과 리프레시 토큰 발급
    access_token = Authorize.create_access_token(subject=user_id)
    refresh_token = Authorize.create_refresh_token(subject=user_id)

    user_info.put("refresh_token", refresh_token)

    await redis().hset(user_id, mapping=user_info, ex=timedelta(days=7))
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
async def logout(response: Response, Authorize: AuthJWT = Depends()):
    user_id = Authorize.get_jwt_subject()

    # 토큰 제거
    response.delete_cookie("refresh_token")
    response.delete_cookie("access_token")
    await redis().delete(user_id)

    return {"message": "로그아웃 성공"}


# 계좌 등록
@app.post("/account")
async def account(Account: AccountModel, Authorize: AuthJWT = Depends()):
    user_id = Authorize.get_jwt_subject()
    await account_register(app.state.db_pool, user_id, Account)

    return {"message": "계좌 등록 성공"}


# 계좌 조회
@app.get("/account/{account_id}")
async def account(account_id: str, Authorize: AuthJWT = Depends()):
    user_id = Authorize.get_jwt_subject()

    account_info = json.loads(await get_account_info(app.state.db_pool, account_id))
    await redis().hset(user_id, "CANO", account_info.get("CANO"))
    await redis().hset(user_id, "ACNT_PRDT_CD", account_info.get("ACNT_PRDT_CD"))

    return {"message": "계좌 조회", "account": account_info}

# 계좌 삭제
@app.delete("/account/{account_id}")
async def account(account_id: str, Authorize: AuthJWT = Depends()):
    user_id = Authorize.get_jwt_subject()

    await account_delete(app.state.db_pool, account_id, user_id)
    return {"message": "계좌 삭제 성공"}


# 계좌 리스트
@app.get("/accounts")
async def accounts(Authorize: AuthJWT = Depends()):
    user_id = Authorize.get_jwt_subject()
    account_list = await get_account_list(app.state.db_pool, user_id)
    return {"message": "계좌 리스트 조회", "accounts": account_list}


# 잔고 조회
@app.get("/balance")
async def stock_balance(Authorize: AuthJWT = Depends()):
    user_id = Authorize.get_jwt_subject()
    cano = await redis().hget(user_id, "CANO")
    acnt_prdt_cd = await redis().hget(user_id, "ACNT_PRDT_CD")

    balance = await get_stock_balance(cano, acnt_prdt_cd)
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

