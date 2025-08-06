# from base64 import b64decode
# from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
# from cryptography.hazmat.backends import default_backend
# from cryptography.hazmat.primitives import padding


# # AES256 복호화(DECODE)
# def aes_cbc_base64_dec(key, iv, cipher_text):
#     """
#     :param key:  str type AES256 secret key value
#     :param iv: str type AES256 Initialize Vector
#     :param cipher_text: Base64 encoded AES256 str
#     :return: Base64-AES256 decodec str
#     """
#     cipher = Cipher(algorithms.AES(key.encode('utf-8')), modes.CBC(iv.encode('utf-8')), backend=default_backend())
#     decryptor = cipher.decryptor()
#     decrypted_data = decryptor.update(b64decode(cipher_text)) + decryptor.finalize()
    
#     unpadder = padding.PKCS7(128).unpadder()
#     unpadded_data = unpadder.update(decrypted_data) + unpadder.finalize()
    
#     return unpadded_data.decode('utf-8')

#
# # 주식체결가
# async def stockspurchase(data_cnt, data):
#     print("============================================")
#     menulist = "유가증권단축종목코드|주식체결시간|주식현재가|전일대비부호|전일대비|전일대비율|가중평균주식가격|주식시가|주식최고가|주식최저가|매도호가1|매수호가1|체결거래량|누적거래량|누적거래대금|매도체결건수|매수체결건수|순매수체결건수|체결강도|총매도수량|총매수수량|체결구분|매수비율|전일거래량대비등락율|시가시간|시가대비구분|시가대비|최고가시간|고가대비구분|고가대비|최저가시간|저가대비구분|저가대비|영업일자|신장운영구분코드|거래정지여부|매도호가잔량|매수호가잔량|총매도호가잔량|총매수호가잔량|거래량회전율|전일동시간누적거래량|전일동시간누적거래량비율|시간구분코드|임의종료구분코드|정적VI발동기준가"
#     menustr = menulist.split('|')
#     pValue = data.split('^')
#     i = 0
#     for cnt in range(data_cnt):     # 넘겨받은 체결데이터 개수만큼 print 한다
#         print("### [%d / %d]"%(cnt+1, data_cnt))
#         for menu in menustr:
#             print("%-13s[%s]" % (menu, pValue[i]))
#             i += 1
#
# # 주식체결통보
# async def stocksigningnotice(data, key, iv):
#     menulist = "고객ID|계좌번호|주문번호|원주문번호|매도매수구분|정정구분|주문종류|주문조건|주식단축종목코드|체결수량|체결단가|주식체결시간|거부여부|체결여부|접수여부|지점번호|주문수량|계좌명|체결종목명|신용구분|신용대출일자|체결종목명40|주문가격"
#     menustr1 = menulist.split('|')
#
#     # AES256 처리 단계
#     aes_dec_str = await aes_cbc_base64_dec(key, iv, data)
#     p_value = aes_dec_str.split('^')
#
#     i = 0
#     for menu in menustr1:
#         print("%s  [%s]" % (menu, p_value[i]))
#         i += 1


# 주식 호가
async def stockhoka(data):
    """
    주식 호가 데이터를 딕셔너리 형태로 변환
    TypeScript StockPriceResponse 타입에 맞춰 구성
    0|H0STASP0|001|005930^140013^0^66000^66100^66200^66300^66400^66500^66600^66700^66800^66900^65900^65800^65700^65600^65500^65400^65300^65200^65100^65000^28408^123373^53451^107830^76471^84039^45688^52373^61717^49894^74727^111951^100474^132318^158455^81601^117285^105583^122745^208814^683244^1213953^0^0^0^0^211181^-66000^5^-100.00^5531414^-30000^0^0^0^0^65950^4927^2'
    """
    recvvalue = data.split('^')  # 수신데이터를 split '^'
    
    # output1 구성 (호가 정보)
    output1 = {
        "aspr_acpt_hour": recvvalue[1],  # 호가 접수 시간
        
        # 매도호가 1~10 (역순으로 저장됨)
        "askp1": recvvalue[3],
        "askp2": recvvalue[4], 
        "askp3": recvvalue[5],
        "askp4": recvvalue[6],
        "askp5": recvvalue[7],
        "askp6": recvvalue[8],
        "askp7": recvvalue[9],
        "askp8": recvvalue[10],
        "askp9": recvvalue[11],
        "askp10": recvvalue[12],
        
        # 매수호가 1~10
        "bidp1": recvvalue[13],
        "bidp2": recvvalue[14],
        "bidp3": recvvalue[15],
        "bidp4": recvvalue[16],
        "bidp5": recvvalue[17],
        "bidp6": recvvalue[18],
        "bidp7": recvvalue[19],
        "bidp8": recvvalue[20],
        "bidp9": recvvalue[21],
        "bidp10": recvvalue[22],
        
        # 매도호가 잔량 1~10 (역순으로 저장됨)
        "askp_rsqn1": recvvalue[23],
        "askp_rsqn2": recvvalue[24],
        "askp_rsqn3": recvvalue[25],
        "askp_rsqn4": recvvalue[26],
        "askp_rsqn5": recvvalue[27],
        "askp_rsqn6": recvvalue[28],
        "askp_rsqn7": recvvalue[29],
        "askp_rsqn8": recvvalue[30],
        "askp_rsqn9": recvvalue[31],
        "askp_rsqn10": recvvalue[32],
        
        # 매수호가 잔량 1~10
        "bidp_rsqn1": recvvalue[33],
        "bidp_rsqn2": recvvalue[34],
        "bidp_rsqn3": recvvalue[35],
        "bidp_rsqn4": recvvalue[36],
        "bidp_rsqn5": recvvalue[37],
        "bidp_rsqn6": recvvalue[38],
        "bidp_rsqn7": recvvalue[39],
        "bidp_rsqn8": recvvalue[40],
        "bidp_rsqn9": recvvalue[41],
        "bidp_rsqn10": recvvalue[42],
        
        # 총 잔량 및 증감
        "total_askp_rsqn": recvvalue[43],      # 총 매도호가 잔량
        "total_bidp_rsqn": recvvalue[44],      # 총 매수호가 잔량
        "total_askp_rsqn_icdc": recvvalue[54], # 총 매도호가 잔량 증감
        "total_bidp_rsqn_icdc": recvvalue[55], # 총 매수호가 잔량 증감
        
        # 시간외 관련
        "ovtm_total_askp_icdc": recvvalue[45], # 시간외 총 매도호가 증감
        "ovtm_total_bidp_icdc": recvvalue[46], # 시간외 총 매수호가 증감
        "ovtm_total_askp_rsqn": recvvalue[56], # 시간외 총 매도호가 잔량
        "ovtm_total_bidp_rsqn": recvvalue[57], # 시간외 총 매수호가 잔량
        
        # 기타
        "ntby_aspr_rsqn": recvvalue[79],       # 순매수 호가 잔량
        "new_mkop_cls_code": recvvalue[58]     # 신 장운영 구분 코드
    }
    
    # output2 구성 (가격 정보)
    output2 = {
        "antc_mkop_cls_code": recvvalue[2],    # 예상 장운영 구분 코드
        "stck_prpr": recvvalue[80],            # 주식 현재가
        "stck_oprc": recvvalue[81],            # 주식 시가
        "stck_hgpr": recvvalue[82],            # 주식 최고가
        "stck_lwpr": recvvalue[83],            # 주식 최저가
        "stck_sdpr": recvvalue[84],            # 주식 기준가
        "antc_cnpr": recvvalue[47],            # 예상 체결가
        "antc_cntg_vrss_sign": recvvalue[51],  # 예상 체결 대비 부호
        "antc_cntg_vrss": recvvalue[50],       # 예상 체결 대비
        "antc_cntg_prdy_ctrt": recvvalue[52],  # 예상 체결 전일 대비율
        "antc_vol": recvvalue[49],             # 예상 거래량
        "stck_shrn_iscd": recvvalue[0],        # 주식 단축 종목코드
        "vi_cls_code": recvvalue[85]           # VI적용구분코드
    }
    
    # 최종 응답 구조
    response = {
        "rt_cd": "0",  # 성공 시 "0"
        "msg_cd": "MCA00000",  # 성공 시 "MCA00000"
        "msg1": "정상처리 되었습니다",
        "output1": output1,
        "output2": output2
    }
    
    return response