"""미국장 시장코드 정식값 및 KIS API 그룹별 매핑

정식코드(canonical) = 시세계열 3글자 NYS/NAS/AMS.
STOCK_INFO/SWING_TRADE 저장값, 클라이언트 전달값, 시세계열 EXCD 로 그대로 사용한다.
거래계열(주문·정정취소·미체결·잔고)만 OVRS_EXCG_CD(4글자)로 변환이 필요하다.
"""

# 정식코드 (STOCK_INFO/SWING_TRADE 저장값, 클라이언트 전달값, 시세계열 EXCD)
US_MARKETS = ("NYS", "NAS", "AMS")

# 클라이언트 글로벌 토글 '미국' 그룹값. 검색/스윙목록/가용자본 파라미터로만 쓰이며
# (해외 여부 판정 용도) — DB 저장값이나 KIS 거래소코드가 아니다.
US_GROUP = "US"

# 거래계열(주문·정정취소·미체결·잔고) OVRS_EXCG_CD 매핑
_EXCG_TRADE = {"NYS": "NYSE", "NAS": "NASD", "AMS": "AMEX"}
# 역매핑: KIS 거래소코드(잔고 응답 등) → 정식코드
_EXCG_TRADE_REVERSE = {v: k for k, v in _EXCG_TRADE.items()}

# 모의투자 잔고: 미국전체(NASD) 미지원 → 거래소별 순회 대상
US_TRADE_EXCG = ("NASD", "NYSE", "AMEX")


def is_overseas(mrkt_code: str) -> bool:
    """미국장(해외) 여부. 거래소 정식코드(NYS/NAS/AMS)와 글로벌 그룹값('US') 모두 해외로 인식"""
    return mrkt_code in US_MARKETS or mrkt_code == US_GROUP


def to_ovrs_excg_cd(mrkt_code: str) -> str:
    """정식코드 → 거래계열 거래소코드(OVRS_EXCG_CD). 이미 4글자면 그대로 통과."""
    return _EXCG_TRADE.get(mrkt_code, mrkt_code)


def from_ovrs_excg_cd(excg_cd: str) -> str:
    """KIS 거래소코드(NYSE/NASD/AMEX) → 정식코드(NYS/NAS/AMS). 이미 정식코드면 그대로 통과."""
    return _EXCG_TRADE_REVERSE.get(excg_cd, excg_cd)
