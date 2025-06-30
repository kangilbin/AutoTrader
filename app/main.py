import logging
from fastapi import FastAPI, Request, Depends, HTTPException, WebSocket
from app.api.KISOpenApi import oauth_token
from app.api.LocalStockApi import get_stock_balance, get_order_cash, get_order_rvsecncl, get_inquire_psbl_rvsecncl_lst
from app.model.schemas.AccountModel import AccountCreate
from app.model.schemas.AuthModel import AuthCreate
from app.model.schemas.ModOrderModel import ModOrder
from app.model.schemas.OrderModel import Order
from app.model.schemas.SwingModel import SwingCreate
from app.model.schemas.UserModel import UserCreate
from app.module.Schedules import schedule_start
from app.module.DBConnection import get_db, Database
from app.module.KisWebSocket import websocket_endpoint
from app.module.RedisConnection import get_redis, Redis
from contextlib import asynccontextmanager
from app.services.AccountService import create_account, get_account, get_accounts, remove_account
from app.services.AuthService import create_auth, get_auth_key, get_auth_keys
from app.services.StockService import get_stock_initial
from app.services.SwingService import create_swing
from app.services.UserService import create_user, login_user, token_refresh, duplicate_user
from sqlalchemy.ext.asyncio import AsyncSession
from app.module.JwtUtils import get_token, TokenData
from fastapi.security import OAuth2PasswordBearer
from typing import Annotated
from app.model.schemas.AuthModel import AuthChoice


logging.basicConfig(level=logging.DEBUG)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await Database.connect()
    await Redis.connect()
    await schedule_start()

    try:
        yield
    finally:
        await Database.disconnect()
        await Redis.disconnect()

app = FastAPI(lifespan=lifespan)

# async def get_current_user(request: Request) -> str:
#     if request.url.path in ["/signup", "/login", "/check_id", "/refresh"]:
#         return None

#     auth_header = request.headers.get("Authorization")
#     if auth_header is None:
#         raise HTTPException(status_code=401, detail="Authorization header missing")

#     try:
#         token = auth_header.split(" ")[1]
#         token_data = verify_token(token)
#         if token_data is None:
#             raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")
#         return token_data.user_id
#     except Exception as e:
#         raise HTTPException(status_code=401, detail=str(e))


# @app.middleware("http")
# async def jwt_auth_middleware(request: Request, call_next):
#     try:
#         await get_current_user(request)
#     except HTTPException as e:
#         return e
#     response = await call_next(request)
#     return response


# 회원 가입
@app.post("/signup")
async def signup(user_data: UserCreate, db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        user = await create_user(db, user_data)
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))
    return {"message": "회원 가입 성공", "data": user}


# 로그인
@app.post("/login")
async def login(user_data: UserCreate, db: Annotated[AsyncSession, Depends(get_db)]):
    user_id = user_data.USER_ID
    user_pw = user_data.PASSWORD

    # 사용자 검증
    access_token, refresh_token = await login_user(db, user_id, user_pw)
    return {"message": "로그인 성공", "access_token": access_token, "refresh_token": refresh_token}


# ID 중복 검사
@app.get("/check_id")
async def check_id(user_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    user_info = await duplicate_user(db, user_id)

    if user_info:
        return {"isDuplicate": True}
    else:
        return {"isDuplicate": False}






# 리프레시 토큰을 이용해 새로운 액세스 토큰 발급
@app.post("/refresh")
async def refresh(request: Request):
    body = await request.json()
    access_token = await token_refresh(body['refresh_token'])
    return {"message": "token 재발급", "access_token": access_token}


# 로그아웃
@app.post("/logout")
async def logout(user_id: Annotated[TokenData, Depends(get_token)]):
    redis = await get_redis()
    # 토큰 제거
    await redis.delete(user_id)

    return {"message": "로그아웃 성공"}


# 보안키 목록 조회
@app.get("/auths")
async def auth(db: Annotated[AsyncSession, Depends(get_db)], user_id: Annotated[TokenData, Depends(get_token)]):
    auth_keys = await get_auth_keys(db, user_id)
    return {"message": "보안키 조회 성공", "data": auth_keys}


# 보안키 등록
@app.post("/auth")
async def auth(auth_data: AuthCreate, db: Annotated[AsyncSession, Depends(get_db)], user_id: Annotated[TokenData, Depends(get_token)]):

    # 디바이스 정보 검증
    await oauth_token(user_id, auth_data.SIMULATION_YN,  auth_data.API_KEY, auth_data.SECRET_KEY)


    try:
        auth_data.USER_ID = user_id
        auth_info = await create_auth(db, auth_data)
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

    return {"message": "보안 등록 완료", "data": auth_info}

# 보안키 선택
@app.post("/auth/choice")
async def auth(auth_data: AuthChoice, db: Annotated[AsyncSession, Depends(get_db)], user_id: Annotated[TokenData, Depends(get_token)]):
    await get_auth_key(db, user_id, auth_data.AUTH_ID, auth_data.ACCOUNT_NO)
    return {"message": "보안 등록 완료"}


# 계좌 등록
@app.post("/account")
async def account(account_data: AccountCreate, db: Annotated[AsyncSession, Depends(get_db)], user_id: Annotated[TokenData, Depends(get_token)]):
    account_data.USER_ID = user_id
    account_info = await create_account(db, account_data)

    return {"message": "계좌 등록 성공", "data": account_info}


# 계좌 조회
@app.get("/account/{account_id}")
async def account(account_id: str, db: Annotated[AsyncSession, Depends(get_db)], user_id: Annotated[TokenData, Depends(get_token)]):
    account_info = await get_account(db, account_id, user_id)
    return {"message": "계좌 조회", "data": account_info}

# 계좌 삭제
@app.delete("/account/{account_id}")
async def account(account_id: str, db: Annotated[AsyncSession, Depends(get_db)], user_id: Annotated[TokenData, Depends(get_token)]):
    await remove_account(db, account_id)
    return {"message": "계좌 삭제 성공"}


# 계좌 리스트
@app.get("/accounts")
async def accounts(db: Annotated[AsyncSession, Depends(get_db)], user_id: Annotated[TokenData, Depends(get_token)]):
    account_list = await get_accounts(db, user_id)
    return {"message": "계좌 리스트 조회", "data": account_list}


# 잔고 조회
@app.get("/balance")
async def stock_balance(db: Annotated[AsyncSession, Depends(get_db)], user_id: Annotated[TokenData, Depends(get_token)]):
    balance = await get_stock_balance(user_id)
    return {"message": "계좌 잔고 조회", "data": balance}


# 종목 코드 조회
@app.get("/stock")
async def stock(query: str, db: Annotated[AsyncSession, Depends(get_db)]):
    stock_info = await get_stock_initial(db, query)
    return {"message": "종목 코드 조회", "data": stock_info}

# 주식 현재가/호가
@app.websocket("/kis_socket")
async def kis_websocket(websocket: WebSocket):
    await websocket_endpoint(websocket)


# 주식 매매 or 매도
@app.post("/order")
async def stock_buy_sell(order: Order, user_id: Annotated[TokenData, Depends(get_token)]):
    order = await get_order_cash(user_id, order)
    return {"message": "주문 완료", "data": order}


# 주식 정정 취소 가능 주문 내역 조회
@app.get("/order/details")
async def stock_order(request: Request, user_id: Annotated[TokenData, Depends(get_token)]):
    fk100 = request.query_params.get("FK100")
    nk100 = request.query_params.get("NK100")

    response = await get_inquire_psbl_rvsecncl_lst(user_id, fk100, nk100)
    return {"message": "주문 내역 조회", "data": response}


# 주식 정정(취소)
@app.post("/order/{order_no}")
async def stock_update_cancel(order: ModOrder, user_id: Annotated[TokenData, Depends(get_token)]):
    response = await get_order_rvsecncl(user_id, order)
    return {"message": "정정 완료", "data": response}


# 스윙 등록
@app.post("/swing")
async def swing_create(swing: SwingCreate, db: Annotated[AsyncSession, Depends(get_db)], user_id: Annotated[TokenData, Depends(get_token)]):
    response = await create_swing(db, swing)
    return {"message": "정정 완료", "data": response}


# 재무제표
# import dart_fss as dart
