from app.api.kis_open_api import oauth_token
from app.module.fetch_api import fetch
from app.module.config import get_env
from app.module.redis_connection import get_redis
from datetime import datetime, timedelta


async def user(user_id: str):
    redis = await get_redis()
    access_data = await redis.hgetall(f"{user_id}_access_token")
    user_data = await redis.hgetall(user_id)

    if not access_data:
        access_data = await oauth_token(user_id, user_data.get("SIMULATION_YN"), user_data.get("API_KEY"), user_data.get("SECRET_KEY"))

    return user_data, access_data


# 현금 잔고 조회
async def get_balance(user_id: str):
    user_data, access_data = await user(user_id)
    path = "uapi/domestic-stock/v1/trading/inquire-psbl-order"
    api_url = f"{access_data.get('api_url')}/{path}"

    if access_data.get("simulation_yn") == "Y":
        # [모의투자]
        tr_id = "VTTC8908R"
    else:
        # [실전투자]
        tr_id = "TTTC8908R"

    headers = {
               "authorization": f"Bearer {access_data.get('access_token')}",
               "appkey": access_data.get("api_key"),
               "appsecret": access_data.get("secret_key"),
               "tr_id": tr_id,
               "custtype": "P",
               }

    params = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "PDNO": "",
        "ORD_UNPR": "",
        "ORD_DVSN": "01",   # 시장가(ORD_DVSN:01)
        "CMA_EVLU_AMT_ICLD_YN": "Y",
        "OVRS_ICLD_YN": "Y"
    }
    response = await fetch("GET", api_url, params=params, headers=headers)
    cash = response['output']['ord_psbl_cash']
    return int(cash)


# 보유 주식
async def get_stock_balance(user_id: str):
    user_data, access_data = await user(user_id)

    path = "/uapi/domestic-stock/v1/trading/inquire-balance"
    if access_data.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
    else:
        url = get_env("REAL_API_URL")

    api_url = f"{url}/{path}"

    if access_data.get("simulation_yn") == "Y":
        # [모의투자]
        tr_id = "VTTC8434R"
    else:
        # [실전투자]
        tr_id = "TTTC8434R"

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": tr_id,
        "custtype": "P"  # B:법인, P:개인
    }
    params = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
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


async def get_order_cash(user_id: str, order: OrderModel):
    """
    주식 주문
    :param user_id:
    :param order: itm_no(종목 번호), qty(주문 수량), ord_dv(매수/매도)
    :return:
    """
    user_data, access_data = await user(user_id)
    if access_data.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
    else:
        url = get_env("REAL_API_URL")
    path = "/uapi/domestic-stock/v1/trading/order-cash"
    api_url = f"{url}/{path}"

    if order.ORD_DV == "buy":
        if access_data.get("simulation_yn") == "Y":
            # [모의투자]
            tr_id = "VTTC0802U"
        else:
            # [실전투자]
            tr_id = "TTTC0012U"
    elif order.ORD_DV == "sell":
        if access_data.get("simulation_yn") == "Y":
        # [모의투자]
            tr_id = "VTTC0801U"
        else:
        # [실전투자]
            tr_id = "TTTC0011U"
    else:
        return None

    if order.ITM_NO == "":
        return None

    if order.QTY == 0:
        return None

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": tr_id,
        "custtype": "P"  # B:법인, P:개인
    }
    params = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],           # 종합계좌번호 8자리
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],  # 계좌상품코드 2자리
        "PDNO": order.ITM_NO,                           # 종목코드(6자리) ETN의 경우, Q로 시작 (EX. Q500001)
        "ORD_DVSN": "01",                               # 주문구분 00:지정가, 01:시장가, 02:조건부지정가  나머지주문구분 API 문서 참조
        "ORD_QTY": str(order.QTY),                      # 주문주식수
        "ORD_UNPR": "0"                                 # 주문단가
    }

    return await fetch("POST", api_url, body=params, headers=headers)


async def get_inquire_psbl_rvsecncl_lst(user_id: str, fk100="", nk100=""):
    """
    주식정정취소가능주문내역
    모의 투자 미지원
    :param user_id:
    :param fk100:
    :param nk100:
    :return:
    """
    user_data, access_data = await user(user_id)

    path = "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"
    api_url = f"{get_env('REAL_API_URL')}/{path}"

    tr_id = "TTTC0084R"

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": tr_id,
        "custtype": "P"  # B:법인, P:개인
    }
    body = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],           # 종합계좌번호 8자리
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],  # 계좌상품코드 2자리
        "INQR_DVSN_1": "1",                     # 조회구분1(정렬순서)  0:조회순서, 1:주문순, 2:종목순
        "INQR_DVSN_2": "0",                     # 조회구분2 0:전체, 1:매도, 2:매수
        "CTX_AREA_FK100": fk100,                # 공란 : 최초 조회시 이전 조회 Output CTX_AREA_FK100 값 : 다음페이지 조회시(2번째부터)
        "CTX_AREA_NK100": nk100                 # 공란 : 최초 조회시 이전 조회 Output CTX_AREA_NK100 값 : 다음페이지 조회시(2번째부터)
    }

    # print(tr_cont, FK100, NK100)
    # if tr_cont == "D" or tr_cont == "E": # 마지막 페이지
    #     print("The End")
    #     current_data = pd.DataFrame(dataframe)
    #     dataframe = current_data
    #     return dataframe
    # elif tr_cont == "F" or tr_cont == "M": # 다음 페이지 존재하는 경우 자기 호출 처리
    #     print('Call Next')
    #     return get_inquire_psbl_rvsecncl_lst("N", FK100, NK100, dataframe)

    return await fetch("POST", api_url, json=body, headers=headers)


async def get_order_rvsecncl(user_id:str, order: ModOrderModel):
    """
    주식 주문(정정취소)
    :param user_id:
    :param order:
        ord_orgno : 주문조직번호
        orgn_odno : 원주문번호
        ord_dvsn : 주문구분
        rvse_cncl_dvsn_cd : 정정 : 01, 취소 : 02
        ord_qty : 주문주식수
        ord_unpr : 주문단가
        qty_all_ord_yn : 잔량전부주문여부 [정정/취소] Y : 잔량전부, N : 잔량일부
    :return:
    """
    user_data, access_data = await user(user_id)
    if access_data.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
    else:
        url = get_env("REAL_API_URL")
    path = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
    api_url = f"{url}/{path}"
    if access_data.get("simulation_yn") == "Y":
        # [모의투자]
        tr_id = "VTTC0803U"
    else:
        # [실전투자]
        tr_id = "TTTC0013U"

    if order.ORD_ORGNO == "":
        print("주문조직번호 확인요망!!!")
        return None

    if order.ORGN_ODNO == "":
        print("원주문번호 확인요망!!!")
        return None

    if order.ORD_DVSN == "":
        print("주문구분 확인요망!!!")
        return None

    if not order.RVSE_CNCL_DVSN_CD in ["01","02"]:
        print("정정취소구분코드 확인요망!!!") # 정정:01. 취소:02
        return None

    if order.QTY_ALL_ORD_YN == "Y" and order.ORD_QTY > 0:
        print("잔량전부 취소/정정주문인 경우 주문수량 0 처리!!!")
        ord_qty = 0

    if order.QTY_ALL_ORD_YN == "N" and order.ORD_QTY == 0:
        print("취소/정정 수량 확인요망!!!")
        return None

    if order.RVSE_CNCL_DVSN_CD == "01" and order.ORD_UNPR == 0:
        print("주문단가 확인요망!!!")
        return None

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": tr_id,
        "custtype": "P"  # B:법인, P:개인
    }
    body = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],           # 종합계좌번호 8자리
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],  # 계좌상품코드 2자리
        "KRX_FWDG_ORD_ORGNO": order.ORD_ORGNO,        # 주문조직번호 API output의 odno(주문번호) 값 입력주문시 한국투자증권 시스템에서 채번된 주문조직번호
        "ORGN_ODNO": order.ORGN_ODNO,                 # 주식일별주문체결조회 API output의 odno(주문번호) 값 입력주문시 한국투자증권 시스템에서 채번된 주문번호
        "ORD_DVSN": order.ORD_DVSN,                   # 주문구분 00:지정가, 01:시장가, 02:조건부지정가  나머지주문구분 API 문서 참조
        "RVSE_CNCL_DVSN_CD": order.RVSE_CNCL_DVSN_CD, # 정정 : 01, 취소 : 02
        "ORD_QTY": str(int(order.ORD_QTY)),           # 주문주식수     [잔량전부 취소/정정주문] "0" 설정 ( QTY_ALL_ORD_YN=Y 설정 ) [잔량일부 취소/정정주문] 취소/정정 수량
        "ORD_UNPR": str(int(order.ORD_UNPR)),         # 주문단가  [정정] 정정주문 1주당 가격 [취소] "0" 설정
        "QTY_ALL_ORD_YN": order.QTY_ALL_ORD_YN        # 잔량전부주문여부 [정정/취소] Y : 잔량전부, N : 잔량일부
    }


    # if str(res.getBody().rt_cd) == "0":
    #     current_data = pd.DataFrame(res.getBody().output, index=[0])
    #     dataframe = current_data
    # else:
    #     print(res.getBody().msg_cd + "," + res.getBody().msg1)
    #     #print(res.getErrorCode() + "," + res.getErrorMessage())

    return await fetch("POST", api_url, json=body, headers=headers)


async def get_inquire_daily_ccld_obj(user_id:str, inqr_strt_dt=None, inqr_end_dt=None, FK100="", NK100=""):
    """
    주식일별주문체결(현황)조회
    :param user_id:
    :param inqr_strt_dt: 시작일자
    :param inqr_end_dt:  종료일자
    :param FK100:
    :param NK100:
    :return:
    """
    user_data, access_data = await user(user_id)
    if access_data.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
    else:
        url = get_env("REAL_API_URL")

    path = '/uapi/domestic-stock/v1/trading/inquire-daily-ccld'
    api_url = f"{url}/{path}"

    if inqr_strt_dt is None:
        inqr_strt_dt = datetime.today().strftime("%Y%m%d")   # 시작일자 값이 없으면 현재일자

    if inqr_end_dt is None:
        inqr_end_dt  = datetime.today().strftime("%Y%m%d")   # 종료일자 값이 없으면 현재일자

    # 시작일자와 현재일자를 datetime 객체로 변환
    current_date = datetime.today()
    # 3개월 전 날짜 계산
    three_months_ago = current_date - timedelta(days=90)
    if datetime.strptime(inqr_strt_dt, "%Y%m%d") > three_months_ago:
        tr_id = "CTSC9215R"  # 02:3개월 이전 국내주식체결내역 (월단위 ex: 2024.04.25 이면 2024.01월이전)
    else:
        tr_id = "TTTC0081R"  # 01:3개월 이내 국내주식체결내역 (월단위 ex: 2024.04.25 이면 2024.01월~04월조회)


    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": tr_id,
        "custtype": "P"  # B:법인, P:개인
    }
    body = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],           # 종합계좌번호 8자리
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],  # 계좌상품코드 2자리
        "INQR_STRT_DT": inqr_strt_dt,           # 조회시작일자
        "INQR_END_DT": inqr_end_dt,             # 조회종료일자
        "SLL_BUY_DVSN_CD": "00",                # 매도매수구분코드 00:전체 01:매도, 02:매수
        "INQR_DVSN": "01",                      # 조회구분(정렬순서)  00:역순, 01:정순
        "PDNO": "",                             # 종목번호(6자리)
        "CCLD_DVSN": "00",                      # 체결구분 00:전체, 01:체결, 02:미체결
        "ORD_GNO_BRNO": "",                     # 사용안함
        "ODNO": "",                             # 주문번호
        "INQR_DVSN_3": "00",                    # 조회구분3 00:전체, 01:현금, 02:융자, 03:대출, 04:대주
        "INQR_DVSN_1": "0",                     # 조회구분1 공란 : 전체, 1 : ELW, 2 : 프리보드
        "CTX_AREA_FK100": FK100,                # 공란 : 최초 조회시 이전 조회 Output CTX_AREA_FK100 값 : 다음페이지 조회시(2번째부터)
        "CTX_AREA_NK100": NK100                 # 공란 : 최초 조회시 이전 조회 Output CTX_AREA_NK100 값 : 다음페이지 조회시(2번째부터)
    }

    return await fetch("POST", api_url, json=body, headers=headers)


async def get_target_price(code: str):
    redis = await get_redis()
    access_data = await redis.hgetall("mgnt_access_token")

    if not access_data:
        access_data = await oauth_token("mgnt", "Y", get_env("API_KEY"), get_env("SECRET_KEY"))

    if access_data.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
    else:
        url = get_env("REAL_API_URL")
    
    path = 'uapi/domestic-stock/v1/quotations/inquire-daily-price'
    api_url = f"{url}/{path}"

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": "FHKST01010400",
    }

    body = {
        "FID_COND_MRKT_DIV_CODE": "J", # J:KRX, NX:NXT, UN:통합
        "FID_INPUT_ISCD	": code,
        "FID_ORG_ADJ_PRC": "1",
        "FID_PERIOD_DIV_CODE": "D"
    }
    response = await fetch("POST", api_url, json=body, headers=headers)

    stck_oprc = int(response['output'][0]['stck_oprc']) #오늘 시가
    stck_hgpr = int(response['output'][0]['stck_hgpr']) #전일 고가
    stck_lwpr = int(response['output'][0]['stck_lwpr']) #전일 저가
    return response['output'][0]


async def get_stock_data(user_id: str, code: str, start_date: str, end_date: str):
    """"
    기간별 데이터 적재
    FID_COND_MRKT_DIV_CODE	조건 시장 분류 코드	String	Y	2	J:KRX, NX:NXT, UN:통합
    FID_INPUT_ISCD	입력 종목코드	String	Y	12	종목코드 (ex 005930 삼성전자)
    FID_INPUT_DATE_1	입력 날짜 1	String	Y	10	조회 시작일자
    FID_INPUT_DATE_2	입력 날짜 2	String	Y	10	조회 종료일자 (최대 100개)
    FID_PERIOD_DIV_CODE	기간분류코드	String	Y	32	D:일봉 W:주봉, M:월봉, Y:년봉
    FID_ORG_ADJ_PRC	수정주가 원주가 가격 여부	String	Y	10	0:수정주가 1:원주가
    """
    user_data, access_data = await user(user_id)
    if access_data.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
    else:
        url = get_env("REAL_API_URL")
    path = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    api_url = f"{url}/{path}"

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": "FHKST03010100",
        "custtype": "P",
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0"
    }

    response = await fetch("GET", api_url, params=params, headers=headers)
    
    # API 응답 데이터의 키를 대문자로 변경하고 st_code 추가
    if response and "output2" in response:
        for item in response["output2"]:
            # 필요한 컬럼만 필터링하여 대문자로 변경
            converted_item = {}
            
            # 테이블에 있는 컬럼만 매핑
            column_mapping = {
                'stck_oprc': 'STCK_OPRC',
                'stck_hgpr': 'STCK_HGPR', 
                'stck_lwpr': 'STCK_LWPR',
                'stck_clpr': 'STCK_CLPR',
                'acml_vol': 'ACML_VOL',
                'stck_bsop_date': 'STCK_BSOP_DATE'
            }
            
            for api_key, db_key in column_mapping.items():
                if api_key in item:
                    converted_item[db_key] = item[api_key]
            
            # st_code 추가
            converted_item["ST_CODE"] = code
            
            # 원본 아이템을 변환된 아이템으로 교체
            item.clear()
            item.update(converted_item)
    
    return response

async def get_inquire_asking_price(user_id: str, code: str):
    """"
    주식호가 조회
    """
    user_data, access_data = await user(user_id)
    path = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
    if access_data.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
    else:
        url = get_env("REAL_API_URL")
    api_url = f"{url}/{path}"

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": "FHKST01010200",
        "custtype": "P",
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
    }

    return await fetch("GET", api_url, params=params, headers=headers)
