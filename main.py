from fastapi import FastAPI, Response, Depends
from api.KISOpenApi import oauth_token
from api.LocalStockApi import get_stock_balance, get_disparity, get_stocks
from depends.header import session_token
from model.RequestModel import Account
from module.DBConnection import DBConnectionPool
from module.RedisClient import redis_client
import uuid
import json


app = FastAPI()

@app.on_event("startup")
async def startup_event():
    app.state.db_pool = DBConnectionPool(max_size=10)

@app.on_event("shutdown")
async def shutdown_event():
    # 애플리케이션 종료 시 모든 커넥션 닫기
    pool = app.state.db_pool
    while pool.pool:
        conn = pool.pool.pop()
        await conn.close()


@app.get("/")
async def root():
    await oauth_token()
    return {"message": "토큰 발행"}


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
async def price(code: str):
    price_info = get_price_info(code);
    return {"message": "주식 현재 시세", "price": price_info}

# 보유 주식
@app.get("/ranking/disparity")
async def disparity(div_cd:str, sort:str):
    ranking = await get_disparity(div_cd,sort)
    return {"message": "이격도 순위 조회", "ranking": ranking}



# 주식 매매


# 주식 매도

