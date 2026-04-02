외국 주식 한국 투자증권 api

해외주식
실전 api 주소
https://openapi.koreainvestment.com:9443

모의 api 주소
https://openapivts.koreainvestment.com:29443

1. 잔고 조회 관련 
- 함수명 : get_stock_balance
- url : /uapi/overseas-stock/v1/trading/inquire-balance
- TR ID : 실전(TTTS3012R), 모의(VTTS3012R)

1-1. request 
- content-type : pplication/json; charset=utf-8
- authorization : 접근토큰
- appkey
- appsecret
- tr_id : 거래ID
- tr_cont : 연속거래여부
- custtype : 고객타입

Query Parameter
- CANO : 계좌번호 체계(8-2)의 앞 8자리
- ACNT_PRDT_CD : 계좌번호 체계(8-2)의 뒤 2자리
- OVRS_EXCG_CD : 해외거래소코드
[모의]
NASD : 나스닥
NYSE : 뉴욕
AMEX : 아멕스

[실전]
NASD : 미국전체
NAS : 나스닥
NYSE : 뉴욕
AMEX : 아멕스

[모의/실전 공통]
SEHK : 홍콩
SHAA : 중국상해
SZAA : 중국심천
TKSE : 일본
HASE : 베트남 하노이
VNSE : 베트남 호치민

- TR_CRCY_CD : 거래통화코드
USD : 미국달러
HKD : 홍콩달러
CNY : 중국위안화
JPY : 일본엔화
VND : 베트남동

- CTX_AREA_FK200 : 연속조회검색조건200
- CTX_AREA_NK200 : 연속조회키200

응답
