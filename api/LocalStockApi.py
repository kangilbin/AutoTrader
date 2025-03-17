from api.KISOpenApi import oauth_token
from model.schemas import OrderModel
from module.FetchAPI import fetch
from module.Config import get_env
from module.RedisConnection import get_redis


# 주식 잔고 조회
async def get_stock_balance(user_id: str):
    user_info = await get_redis().hgetall(user_id)
    access_token = await get_redis().get(f"{user_id}_access_token")

    if not access_token:
        response = await oauth_token(user_id, user_info.get("API_KEY"), user_info.get("SECRET_KEY"))
        access_token = response.get("access_token")

    path = "/uapi/domestic-stock/v1/trading/inquire-balance"
    api_url = f"{get_env('API_URL')}/{path}"

    headers = {
        "authorization": f"Bearer {access_token}",
        "appkey": user_info.get("API_KEY"),
        "appsecret": user_info.get("SECRET_KEY"),
        "tr_id": "TTTC8434R",
        "custtype": "P"  # B:법인, P:개인
    }
    params = {
        "CANO": user_info.get("CANO"),
        "ACNT_PRDT_CD": user_info.get("ACNT_PRDT_CD"),
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": ""
    }

    return await fetch("GET", api_url, params=params, headers=headers)


# 주식 주문
# ord_dv : buy(매수), sell(매도)
# itm_no : 종목번호
# qty : 주문수량
# unpr : 주문단가
async def get_order_cash(user_id: str, order: OrderModel):
    user_info = await get_redis().hgetall(user_id)
    access_token = await get_redis().get(f"{user_id}_access_token")

    if not access_token:
        response = await oauth_token(user_id, user_info.get("API_KEY"), user_info.get("SECRET_KEY"))
        access_token = response.get("access_token")

    path = "/uapi/domestic-stock/v1/trading/order-cash"
    api_url = f"{get_env('API_URL')}/{path}"

    if order.ORD_DV == "buy":
        tr_id = "TTTC0802U" # 주식 현금 매수 주문    [모의투자] VTTC0802U : 주식 현금 매수 주문
    elif order.ORD_DV == "sell":
        tr_id = "TTTC0801U" # 주식 현금 매도 주문    [모의투자] VTTC0801U : 주식 현금 매도 주문
    else:
        return None

    if order.ITM_NO == "":
        return None

    if order.QTY == 0:
        return None

    if order.UNPR == 0:
        return None

    headers = {
        "authorization": f"Bearer {access_token}",
        "appkey": user_info.get("API_KEY"),
        "appsecret": user_info.get("SECRET_KEY"),
        "tr_id": tr_id,
        "custtype": "P"  # B:법인, P:개인
    }
    params = {
        "CANO": user_info.get("CANO"),                  # 종합계좌번호 8자리
        "ACNT_PRDT_CD": user_info.get("ACNT_PRDT_CD"),  # 계좌상품코드 2자리
        "PDNO": order.ITM_NO,                                 # 종목코드(6자리) ETN의 경우, Q로 시작 (EX. Q500001)
        "ORD_DVSN": "01",                               # 주문구분 00:지정가, 01:시장가, 02:조건부지정가  나머지주문구분 API 문서 참조
        "ORD_QTY": str(order.QTY),                       # 주문주식수
        "ORD_UNPR": str(order.UNPR)                      # 주문단가
    }

    return await fetch("POST", api_url, params=params, headers=headers)


# 주식 주문(정정취소)
async def get_order_rvsecncl(ord_orgno="", orgn_odno="", ord_dvsn="", rvse_cncl_dvsn_cd="", ord_qty=0, ord_unpr=0, qty_all_ord_yn="", tr_cont="", dataframe=None):  # 국내주식주문 > 주식주문(정정취소)
    url = '/uapi/domestic-stock/v1/trading/order-rvsecncl'
    tr_id = "TTTC0803U"  # 주식 정정 취소 주문    [모의투자] VTTC0803U : 주식 정정 취소 주문

    if ord_orgno == "":
        print("주문조직번호 확인요망!!!")
        return None

    if orgn_odno == "":
        print("원주문번호 확인요망!!!")
        return None

    if ord_dvsn == "":
        print("주문구분 확인요망!!!")
        return None

    if not rvse_cncl_dvsn_cd in ["01","02"]:
        print("정정취소구분코드 확인요망!!!") # 정정:01. 취소:02
        return None

    if qty_all_ord_yn == "Y" and ord_qty > 0:
        print("잔량전부 취소/정정주문인 경우 주문수량 0 처리!!!")
        ord_qty = 0

    if qty_all_ord_yn == "N" and ord_qty == 0:
        print("취소/정정 수량 확인요망!!!")
        return None

    if rvse_cncl_dvsn_cd == "01" and ord_unpr == 0:
        print("주문단가 확인요망!!!")
        return None

    params = {
        "CANO": kis.getTREnv().my_acct,         # 종합계좌번호 8자리
        "ACNT_PRDT_CD": kis.getTREnv().my_prod, # 계좌상품코드 2자리
        "KRX_FWDG_ORD_ORGNO": ord_orgno,        # 주문조직번호 API output의 odno(주문번호) 값 입력주문시 한국투자증권 시스템에서 채번된 주문조직번호
        "ORGN_ODNO": orgn_odno,                 # 주식일별주문체결조회 API output의 odno(주문번호) 값 입력주문시 한국투자증권 시스템에서 채번된 주문번호
        "ORD_DVSN": ord_dvsn,                   # 주문구분 00:지정가, 01:시장가, 02:조건부지정가  나머지주문구분 API 문서 참조
        "RVSE_CNCL_DVSN_CD": rvse_cncl_dvsn_cd, # 정정 : 01, 취소 : 02
        "ORD_QTY": str(int(ord_qty)),           # 주문주식수     [잔량전부 취소/정정주문] "0" 설정 ( QTY_ALL_ORD_YN=Y 설정 ) [잔량일부 취소/정정주문] 취소/정정 수량
        "ORD_UNPR": str(int(ord_unpr)),         # 주문단가  [정정] 정정주문 1주당 가격 [취소] "0" 설정
        "QTY_ALL_ORD_YN": qty_all_ord_yn        # 잔량전부주문여부 [정정/취소] Y : 잔량전부, N : 잔량일부
    }

    res = kis._url_fetch(url, tr_id, tr_cont, params, postFlag=True)

    if str(res.getBody().rt_cd) == "0":
        current_data = pd.DataFrame(res.getBody().output, index=[0])
        dataframe = current_data
    else:
        print(res.getBody().msg_cd + "," + res.getBody().msg1)
        #print(res.getErrorCode() + "," + res.getErrorMessage())
        dataframe = None

    return dataframe
