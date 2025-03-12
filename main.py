from fastapi import FastAPI, Response, Request, Depends, HTTPException
from api.KISOpenApi import oauth_token
from api.LocalStockApi import get_stock_balance
from model.schemas.AccountModel import AccountCreate
from model.schemas.UserModel import UserCreate
from module.DBConnection import get_db
from module.RedisConnection import redis_pool, redis
from contextlib import asynccontextmanager
from queries.KIS_LOCAL_STOCKS import get_stocks
from fastapi_jwt_auth import AuthJWT
from model.schemas.JwtModel import Settings
import websockets
from services.AccountService import create_account, get_account, get_accounts, remove_account
from services.UserService import create_user, login_user, refresh_token


# 의존성 주입을 위한 설정
@AuthJWT.load_config
def get_config():
    return Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = get_db()
    app.state.redis_pool = await redis_pool(max_size=10)
    app.state.websocket = await websockets.connect('ws://ops.koreainvestment.com:21000')
    try:
        yield
    finally:
        await app.state.db_pool.close()
        await app.state.redis_pool.close()
        await app.state.redis_pool.wait_closed()
        await app.state.websocket.close()

app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def jwt_auth_middleware(request: Request, call_next):
    try:
        # Authorization 헤더에서 토큰 추출
        auth_header = request.headers.get("Authorization")
        if auth_header is None:
            raise HTTPException(status_code=401, detail="Authorization header missing")

        # JWT 토큰 인증
        Authorize = AuthJWT()
        Authorize.jwt_required()  # 토큰 검증

    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

    response = await call_next(request)
    return response


# 회원 가입
@app.post("/signup")
async def signup(user_data: UserCreate):
    # 디바이스 정보 검증
    response = await oauth_token(user_data.API_KEY, user_data.SECRET_KEY)

    token = response.get("access_token")
    if (not token) or (response.get("error_code")):
        return {"message": response.get("error_description"), "code": response.get("error_code")}

    try:
        user = await create_user(app.state.db_pool, user_data)
    except Exception as e:
        return {"message": "오류", "error": str(e)}

    return {"message": "회원 가입 성공", "data": user}

# 로그인
@app.post("/login")
async def login(request: Request, response: Response, authorize: AuthJWT = Depends()):
    req = await request.json()
    user_id = req.get("USER_ID")
    user_pw = req.get("PASSWORD")

    # 사용자 검증
    access_token, refresh_token, user_info = await login_user(app.state.db_pool, user_id, user_pw, authorize)

    response.set_cookie(key="access_token", value=access_token, httponly=True)
    response.set_cookie(key="refresh_token", value=refresh_token, httponly=True)

    return {"message": "로그인 성공"}


# 리프레시 토큰을 이용해 새로운 액세스 토큰 발급
@app.post("/refresh")
async def refresh(request: Request, authorize: AuthJWT = Depends()):
    token = request.cookies.get("refresh_token")
    return await refresh_token(token, authorize)

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
async def account(account_data: AccountCreate, authorize: AuthJWT = Depends()):
    user_id = authorize.get_jwt_subject()
    account_data.USER_ID = user_id
    await create_account(app.state.db_pool, account_data)

    return {"message": "계좌 등록 성공"}


# 계좌 조회
@app.get("/account/{account_id}")
async def account(account_id: str, authorize: AuthJWT = Depends()):
    user_id = authorize.get_jwt_subject()
    account_info = await get_account(app.state.db_pool, account_id, user_id)
    return {"message": "계좌 조회", "data": account_info}


# 계좌 삭제
@app.delete("/account/{account_id}")
async def account(account_id: str, Authorize: AuthJWT = Depends()):
    user_id = Authorize.get_jwt_subject()
    await remove_account(app.state.db_pool, account_id, user_id)
    return {"message": "계좌 삭제 성공"}


# 계좌 리스트
@app.get("/accounts")
async def accounts(authorize: AuthJWT = Depends()):
    user_id = authorize.get_jwt_subject()
    account_list = await get_accounts(app.state.db_pool, user_id)
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

