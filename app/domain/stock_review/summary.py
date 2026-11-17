"""
종목 리뷰용 데이터 요약 — KIS·DART 원본 응답을 Gemini 프롬프트용 dict로 줄인다.

순수 함수만 둔다 (DB·네트워크·Redis 없음). LLM은 원시 숫자 수백 개를 정확히
읽지 못하므로 지표를 여기서 계산해 넘기고, 오독하기 쉬운 값(DART 분기 단독/누적 구분,
4분기 파생, 깨진 지표값 #####, KIS의 결측 표시 99.99)은 여기서 정리한다.
"""
import re
from datetime import date
from typing import Optional

import numpy as np
import talib as ta

# KIS 가 값이 없는 항목에 채워 보내는 표시 (재무 TR 종료 후에도 현재가 등 다른 TR 에서 쓰인다)
_MISSING = "99.99"

# 주식현재가 iscd_stat_cls_code
_STAT_MANAGED = "51"       # 관리종목
_STAT_INVEST_RISK = "52"   # 투자위험
_STAT_INVEST_WARN = "53"   # 투자경고
_STAT_INVEST_CAUTION = "54"  # 투자주의
_STAT_HALTED = "58"        # 거래정지
_STAT_OVERHEATED = "59"    # 단기과열

# 주식현재가 mrkt_warn_cls_code
_WARN_CAUTION = "01"
_WARN_WARNING = "02"
_WARN_RISK = "03"

# 종목 하나가 아니라 시장 전체를 훑는 묶음 기사 — 종목코드가 붙어 와도 정보가 없다
_NEWS_NOISE = re.compile(
    r"증시요약|상위종목|상위\s*\d+\s*종목|마감시황|특징\s*(종목|상한가)|"
    r"현재\s*코스(피|닥)|매수우위|매도우위|(오전|오후)장\s*특징주"
)

# KIS 뉴스 제목에서 가운뎃점(·)이 SUB(0x1A)로 깨져 온다 ("금융위\x1a금감원")
_BROKEN_MIDDLE_DOT = "\x1a"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")



def _num(value) -> Optional[float]:
    """KIS 문자열 숫자 → float. 빈 값·결측 표시는 None"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == _MISSING:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return round(float(value), digits)


def _pct(now: Optional[float], base: Optional[float]) -> Optional[float]:
    if now is None or not base:
        return None
    return _round((now - base) / base * 100)


# ============================================================
# 종목 상태 (사전 차단 / 경고)
# ============================================================

def blocking_reasons(quote: dict) -> list[str]:
    """스윙 등록 자체가 부적합한 상태 — 하나라도 있으면 Gemini를 부르지 않는다"""
    stat = quote.get("iscd_stat_cls_code")
    warn = quote.get("mrkt_warn_cls_code")
    reasons = []
    if stat == _STAT_MANAGED or quote.get("mang_issu_cls_code") == "Y":
        reasons.append("관리종목")
    if stat == _STAT_INVEST_RISK or warn == _WARN_RISK:
        reasons.append("투자위험종목")
    if stat == _STAT_HALTED or quote.get("temp_stop_yn") == "Y":
        reasons.append("거래정지")
    if quote.get("sltr_yn") == "Y":
        reasons.append("정리매매")
    return reasons


def warning_flags(quote: dict) -> list[str]:
    """등록은 가능하지만 판단에 반영해야 할 시장 경고"""
    stat = quote.get("iscd_stat_cls_code")
    warn = quote.get("mrkt_warn_cls_code")
    flags = []
    if stat == _STAT_INVEST_WARN or warn == _WARN_WARNING:
        flags.append("투자경고")
    if stat == _STAT_INVEST_CAUTION or warn == _WARN_CAUTION:
        flags.append("투자주의")
    if stat == _STAT_OVERHEATED:
        flags.append("단기과열")
    if quote.get("short_over_yn") == "Y":
        flags.append("공매도 과열")
    if quote.get("invt_caful_yn") == "Y":
        flags.append("투자유의")
    return flags


# ============================================================
# 현재가 / 밸류에이션
# ============================================================

def summarize_quote(quote: dict) -> dict:
    """주식현재가 시세 output → 밸류에이션·위치 요약"""
    return {
        "업종": quote.get("bstp_kor_isnm"),
        "대표시장": quote.get("rprs_mrkt_kor_name"),
        "현재가": _num(quote.get("stck_prpr")),
        "전일대비_pct": _num(quote.get("prdy_ctrt")),
        "시가총액_억원": _num(quote.get("hts_avls")),
        # 현재가 TR의 PER/EPS 등은 직전 결산(연간) 기준 — 재무비율의 연 누적 EPS 와 섞어 읽지 않게 이름에 기준을 붙인다
        "PER_직전결산기준": _num(quote.get("per")),
        "PBR_직전결산기준": _num(quote.get("pbr")),
        "EPS_직전결산": _num(quote.get("eps")),
        "BPS_직전결산": _num(quote.get("bps")),
        "외국인보유율_pct": _num(quote.get("hts_frgn_ehrt")),
        "신용잔고율_pct": _num(quote.get("whol_loan_rmnd_rate")),
        "결산월": quote.get("stac_month"),
        "250일최고_대비_pct": _num(quote.get("d250_hgpr_vrss_prpr_rate")),
        "250일최저_대비_pct": _num(quote.get("d250_lwpr_vrss_prpr_rate")),
        "시장경고": warning_flags(quote),
    }


# ============================================================
# 일봉 — 일반 투자 관점 요약
# ============================================================
# 매매 전략 지표(EMA20 괴리·OBV·ADX 등)는 넣지 않는다. 그 지표를 주면 모델이 투자 판단을
# '전략 진입 조건' 관점으로만 설명했다. 일반 투자자가 보는 흐름·위치·변동성만 넘긴다.

def _bar_arrays(bars: list[dict]):
    rows = sorted(bars, key=lambda b: str(b["STCK_BSOP_DATE"]))
    dates = [str(b["STCK_BSOP_DATE"]) for b in rows]
    c, v = (
        np.array([float(b[k]) for b in rows], dtype=float)
        for k in ("STCK_CLPR", "ACML_VOL")
    )
    h = np.array([float(b["STCK_HGPR"]) for b in rows], dtype=float)
    l = np.array([float(b["STCK_LWPR"]) for b in rows], dtype=float)
    return dates, h, l, c, v


def _last(arr) -> Optional[float]:
    if arr is None or len(arr) == 0:
        return None
    return _round(arr[-1])


def _monthly(dates: list[str], close: np.ndarray) -> list[dict]:
    """월별 마지막 종가와 전월 대비 등락 — 6개월 흐름을 한눈에 (첫 달은 기간 중간부터라 등락 없음)"""
    last_by_month: dict[str, float] = {}
    for d, c in zip(dates, close):
        last_by_month[f"{d[:4]}-{d[4:6]}"] = float(c)
    months = sorted(last_by_month)
    return [
        {"월": m, "월말종가": _round(last_by_month[m]),
         "등락_pct": _pct(last_by_month[m], last_by_month[months[i - 1]]) if i > 0 else None}
        for i, m in enumerate(months)
    ]


def _max_drawdown_pct(close: np.ndarray) -> Optional[float]:
    """기간 중 고점 대비 가장 크게 빠졌던 폭 (%)"""
    if len(close) < 2:
        return None
    peaks = np.maximum.accumulate(close)
    return _round(float(((close - peaks) / peaks).min()) * 100)


def summarize_price(bars: list[dict], last_bar_incomplete: bool = False) -> dict:
    """6개월 일봉 → 흐름·위치·변동성·거래량 요약 (일반 투자 관점)

    Args:
        bars: STCK_BSOP_DATE/STCK_HGPR/STCK_LWPR/STCK_CLPR/ACML_VOL 행 (순서 무관)
        last_bar_incomplete: 마지막 봉이 장중 미완성인지 (요약에도 그 값이 반영됨)
    """
    if not bars:
        return {"봉수": 0}

    dates, high, low, close, vol = _bar_arrays(bars)
    n = len(close)
    last = close[-1]

    def ret(days: int) -> Optional[float]:
        return _pct(last, close[-days - 1]) if n > days else None

    def vs_ma(days: int) -> Optional[float]:
        return _pct(last, float(close[-days:].mean())) if n >= days else None

    hi6, lo6 = float(high.max()), float(low.min())
    daily = np.diff(close) / close[:-1] if n > 1 else np.array([])
    recent_daily = daily[-60:]
    rsi = ta.RSI(close, timeperiod=14) if n > 14 else None

    return {
        "봉수": n,
        "기간": f"{dates[0]}~{dates[-1]}",
        "마지막봉_장중미완성": last_bar_incomplete,
        "종가": _round(last),
        "수익률_pct": {"1개월": ret(21), "3개월": ret(63), "6개월": _pct(last, close[0])},
        "6개월_최고가": _round(hi6),
        "6개월_최저가": _round(lo6),
        "6개월최고_대비_pct": _pct(last, hi6),
        # 0 = 6개월 최저, 100 = 6개월 최고
        "6개월범위_위치_pct": _round((last - lo6) / (hi6 - lo6) * 100) if hi6 > lo6 else None,
        "이동평균_대비_pct": {"20일": vs_ma(20), "60일": vs_ma(60), "120일": vs_ma(120)},
        "6개월_최대낙폭_pct": _max_drawdown_pct(close),
        "일간변동성_최근60일_pct": _round(float(recent_daily.std()) * 100) if len(recent_daily) > 1 else None,
        "RSI14": _last(rsi),
        # 최근 한 달 거래가 6개월 평소보다 활발한가 (1보다 크면 관심 증가)
        "거래량_최근1개월_6개월평균대비_배": (
            _round(float(vol[-21:].mean()) / float(vol.mean())) if n > 21 and vol.mean() else None
        ),
        "월별흐름": _monthly(dates, close),
        "최근10일": [
            {"일자": dates[i], "종가": _round(close[i]),
             "등락_pct": _pct(close[i], close[i - 1]) if i > 0 else None}
            for i in range(max(0, n - 10), n)
        ],
    }


# ============================================================
# 재무 (DART)
# ============================================================
# 분기·반기 보고서의 thstrm_amount 는 그 분기 단독 값이고 thstrm_add_amount 가 연 누적이다.
# 사업보고서는 연간 값뿐이라 4분기 단독 값은 '연간 − 같은 해 3분기 누적'으로 구한다.
# 결산월은 계정 행의 기간 문자열("2026.01.01 ~ 2026.09.30", "2026.09.30 현재")에서 읽는다 — 12월 결산이 아닌 회사도 맞는다.

_Q1, _HALF, _Q3, _ANNUAL = "11013", "11012", "11014", "11011"
_QUARTER_NAME = {_Q1: "1분기", _HALF: "반기", _Q3: "3분기", _ANNUAL: "사업보고서"}

_IS = "IS"
_ACCOUNT_NAMES = {
    "매출액": ("매출액", "영업수익", "수익(매출액)"),
    "영업이익": ("영업이익", "영업이익(손실)"),
    "당기순이익": ("당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익"),
}
_EOK = 1e8
_DATE = re.compile(r"(\d{4})\.(\d{2})\.\d{2}")

# 지표는 이름이 아니라 코드로 고른다 (이름은 분류 안에서 겹치거나 바뀔 수 있다)
_INDICATORS = {
    "수익성": {"M211200": "순이익률_pct", "M211300": "매출총이익률_pct", "M211550": "ROE_pct"},
    "안정성": {"M221100": "부채비율_pct", "M221200": "유동비율_pct", "M221000": "자기자본비율_pct"},
    "성장성": {"M231000": "매출액증가율_pct", "M231400": "영업이익증가율_pct", "M231800": "순이익증가율_pct"},
}


def _amount(value) -> Optional[float]:
    """DART 금액 문자열("1,527,344,973,381") → float(원). 빈 값·'-'는 None"""
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _account(rows: list[dict], key: str) -> Optional[dict]:
    """손익계산서(IS) 행 중 계정명이 맞는 첫 행 (당기순이익은 같은 이름이 두 번 나오는데 값은 같다)"""
    names = _ACCOUNT_NAMES[key]
    return next((r for r in rows if r.get("sj_div") == _IS and r.get("account_nm") in names), None)


def _period_end(rows: list[dict], field: str = "thstrm_dt") -> Optional[str]:
    """계정 행의 기간 문자열에서 마지막 날짜의 연-월 ("2026.01.01 ~ 2026.09.30" → "2026-09")"""
    for row in rows:
        dates = _DATE.findall(row.get(field) or "")
        if dates:
            year, month = dates[-1]
            return f"{year}-{month}"
    return None


def _eok(value: Optional[float]) -> Optional[float]:
    return None if value is None else _round(value / _EOK, 1)


def _margin(op: Optional[float], sales: Optional[float]) -> Optional[float]:
    return _round(op / sales * 100, 1) if op is not None and sales not in (None, 0) else None


def _quarter_item(period: Optional[str], sales, op, net) -> dict:
    return {"결산월": period, "매출액_억원": _eok(sales), "영업이익_억원": _eok(op),
            "당기순이익_억원": _eok(net), "영업이익률_pct": _margin(op, sales)}


def quarterly_income(reports: list[dict], limit: int = 5) -> list[dict]:
    """제출된 보고서(최신 먼저) → 개별 분기 손익 (최신 먼저)

    4분기는 사업보고서 연간 값에서 같은 해 3분기 누적을 뺀다. 3분기 보고서가 없으면 계산할 수 없어 건너뛴다.
    """
    q3_cum = {}  # 사업연도 → 3분기 누적 (매출·영업이익·순이익)
    for rep in reports:
        if rep["reprt_code"] == _Q3:
            q3_cum[rep["year"]] = tuple(
                _amount((_account(rep["rows"], k) or {}).get("thstrm_add_amount")) for k in _ACCOUNT_NAMES
            )

    result = []
    for rep in reports:
        values = tuple(_amount((_account(rep["rows"], k) or {}).get("thstrm_amount")) for k in _ACCOUNT_NAMES)
        if all(v is None for v in values):
            continue
        if rep["reprt_code"] == _ANNUAL:
            cum = q3_cum.get(rep["year"])
            if cum is None:
                continue
            values = tuple(a - c if a is not None and c is not None else None for a, c in zip(values, cum))
        result.append(_quarter_item(_period_end(rep["rows"]), *values))
    return result[:limit]


def annual_income(reports: list[dict], limit: int = 3) -> list[dict]:
    """가장 최근 사업보고서의 당기·전기·전전기 연간 손익 (최신 먼저)"""
    annual = next((r for r in reports if r["reprt_code"] == _ANNUAL), None)
    if not annual:
        return []
    rows = annual["rows"]
    result = []
    for amount_field, date_field in (("thstrm_amount", "thstrm_dt"), ("frmtrm_amount", "frmtrm_dt"),
                                     ("bfefrmtrm_amount", "bfefrmtrm_dt")):
        values = tuple(_amount((_account(rows, k) or {}).get(amount_field)) for k in _ACCOUNT_NAMES)
        if all(v is None for v in values):
            continue
        result.append(_quarter_item(_period_end(rows, date_field), *values))
    return result[:limit]


def indicators(rows: list[dict]) -> dict:
    """최신 보고서의 재무지표 {수익성: {...}, 안정성: {...}, 성장성: {...}} — 결측·깨진 값(#####)은 None"""
    by_code = {r.get("idx_code"): _num(r.get("idx_val")) for r in rows}
    return {
        group: {label: _round(by_code.get(code), 1) for code, label in fields.items()}
        for group, fields in _INDICATORS.items()
    }


def summarize_financials(data: dict) -> dict:
    """DART 보고서 묶음({"reports", "indicators"}) → 프롬프트용 재무. 보고서가 없으면 그 사실을 남긴다"""
    reports = data.get("reports") or []
    if not reports:
        return {"조회실패": "DART 에 제출된 재무 보고서가 없다(신규 상장 등) — 재무 판단은 PER·EPS 등 현재가 지표로만 한다"}
    latest = reports[0]
    return {
        "기준보고서": f"{latest['year']}년 {_QUARTER_NAME.get(latest['reprt_code'], '')} ({_period_end(latest['rows'])} 결산)",
        "분기실적_개별분기": quarterly_income(reports),
        "연간실적_확정": annual_income(reports),
        "재무지표_최근보고서_연초누적기준": indicators(data.get("indicators") or []),
    }


# ============================================================
# 투자자 동향
# ============================================================

_INVESTORS = {"frgn": "외국인", "orgn": "기관", "prsn": "개인"}


def summarize_investors(rows: list[dict], first_row_incomplete: bool = False) -> dict:
    """투자자별 순매수 금액(백만원) — 5일/20일 누적 + 최근 10일 (최신 먼저)"""
    rows = sorted((r for r in rows if r.get("stck_bsop_date")), key=lambda r: r["stck_bsop_date"], reverse=True)

    def total(key: str, days: int) -> Optional[float]:
        values = [_num(r.get(f"{key}_ntby_tr_pbmn")) for r in rows[:days]]
        values = [v for v in values if v is not None]
        return _round(sum(values)) if values else None

    return {
        "단위": "백만원",
        "최신일_장중집계": first_row_incomplete,
        "누적순매수": {
            f"{label}_{days}일": total(key, days)
            for key, label in _INVESTORS.items() for days in (5, 20)
        },
        "최근10일": [
            {"일자": r["stck_bsop_date"],
             **{label: _num(r.get(f"{key}_ntby_tr_pbmn")) for key, label in _INVESTORS.items()}}
            for r in rows[:10]
        ],
    }


# ============================================================
# 뉴스 / 공시
# ============================================================

_WEEKDAYS = "월화수목금토일"


def _news_date(yyyymmdd: Optional[str]) -> Optional[str]:
    """20261020 → '2026-10-20 (화)' — 모델이 날짜·요일을 바로 읽도록"""
    if not yyyymmdd or len(yyyymmdd) != 8:
        return yyyymmdd
    try:
        d = date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:]))
    except ValueError:
        return yyyymmdd
    return f"{d.isoformat()} ({_WEEKDAYS[d.weekday()]})"


def _news_time(hhmmss: Optional[str]) -> Optional[str]:
    return f"{hhmmss[:2]}:{hhmmss[2:4]}" if hhmmss and len(hhmmss) >= 4 else hhmmss


def _is_disclosure(row: dict) -> bool:
    """공시 (출처가 '코스닥 공시' 등) — 하루 안에서 일반 기사보다 먼저 담는다"""
    return "공시" in (row.get("dorg") or "")


def filter_news(rows: list[dict], code: str, name: str = "",
                stock_limit: int = 30, market_limit: int = 10,
                stock_per_day: int = 5, market_per_day: int = 2) -> dict:
    """뉴스·공시 제목을 종목 기사 / 시장·업종 기사로 나눠 날짜별로 고르게 담는다 (최신 날짜 먼저)

    news-title TR은 FID_INPUT_ISCD로 관련 기사를 제대로 골라 주지만, 응답의
    iscd1~10 에는 관련 종목 일부만 실린다 — "에코프로비엠, 유상증자 청약률…"이
    086520(에코프로)만 달고 오는 식이다. 그래서 응답 종목코드만으로 거르면
    핵심 기사를 버린다. 코드가 붙었거나 제목에 종목명이 있으면 종목 기사,
    나머지는 KIS가 관련으로 분류한 시장·업종 기사로 따로 넘긴다.

    기사가 많은 종목(삼성전자 등)은 하루에만 40건이 넘어 최신순으로 자르면 오늘 기사만 남고
    며칠 전 실적·공시가 빠진다. 그래서 하루 상한(stock_per_day)을 두어 최근 며칠에 고르게 나누고,
    하루 안에서는 공시를 먼저 담는다.
    """
    seen = set()
    stock_by_day: dict[str, list] = {}
    market_by_day: dict[str, list] = {}
    # 날짜 최신순, 같은 날은 공시 먼저 → 시각 최신순
    ordered = sorted(
        rows,
        key=lambda r: (r.get("data_dt", ""), _is_disclosure(r), r.get("data_tm", "")),
        reverse=True,
    )
    for r in ordered:
        title = (r.get("hts_pbnt_titl_cntt") or "").replace(_BROKEN_MIDDLE_DOT, "·")
        title = _CONTROL_CHARS.sub("", title).strip()
        if not title or _NEWS_NOISE.search(title):
            continue
        key = re.sub(r"\s+", "", title)
        if key in seen:
            continue
        seen.add(key)

        day = r.get("data_dt") or ""
        item = {"일자": _news_date(day), "시각": _news_time(r.get("data_tm")),
                "출처": r.get("dorg"), "제목": title}
        tagged = code in {r.get(f"iscd{i}") for i in range(1, 11)}
        if tagged or (name and name in title):
            bucket, cap = stock_by_day.setdefault(day, []), stock_per_day
        else:
            bucket, cap = market_by_day.setdefault(day, []), market_per_day
        if len(bucket) < cap:
            bucket.append(item)

    def flatten(by_day: dict, limit: int) -> list:
        result = []
        for day in sorted(by_day, reverse=True):
            result.extend(by_day[day])
        return result[:limit]

    return {"종목": flatten(stock_by_day, stock_limit), "시장_업종": flatten(market_by_day, market_limit)}


# ============================================================
# 미국 종목 (KIS 해외 현재가·뉴스 + Finnhub 재무)
# ============================================================
# 미국에는 투자자별 순매수 같은 공개 데이터가 없다 — 수급은 일봉 지표(거래량·OBV)로 본다.

_US_TRADABLE = "매매 가능"
# 종목 코드로 조회해도 여러 종목을 묶은 기사가 온다 ("[개장 전 특징주] 스틸 다이내믹스…"가 AAPL 로 옴)
_US_NEWS_NOISE = re.compile(r"특징주|주요 종목에 대한|마감 시황|장 마감|개장 전 체크")


def us_blocking_reasons(quote: dict) -> list[str]:
    """해외 현재가상세 e_ordyn — '매매 가능'이 아니면 등록 대상이 아니다 (거래정지 등)"""
    status = (quote.get("e_ordyn") or "").strip()
    return [f"매매 불가({status})"] if status and status != _US_TRADABLE else []


def summarize_us_quote(quote: dict) -> dict:
    """해외 현재가상세(HHDFS76200200) → 밸류에이션·위치 요약 (금액 USD)"""
    last, base = _num(quote.get("last")), _num(quote.get("base"))
    return {
        "업종": quote.get("e_icod"),
        "현재가_USD": last,
        "전일대비_pct": _pct(last, base),
        "시가총액_USD": _num(quote.get("tomv")),
        "PER": _num(quote.get("perx")),
        "PBR": _num(quote.get("pbrx")),
        "EPS_USD": _num(quote.get("epsx")),
        "BPS_USD": _num(quote.get("bpsx")),
        "52주최고_대비_pct": _pct(last, _num(quote.get("h52p"))),
        "52주최저_대비_pct": _pct(last, _num(quote.get("l52p"))),
    }


# Finnhub metric 키 → 프롬프트용 이름 (TTM = 최근 4분기 합산)
_US_METRICS = {
    "peTTM": "PER_TTM",
    "pbQuarterly": "PBR",
    "psTTM": "PSR_TTM",
    "epsTTM": "EPS_TTM_USD",
    "epsGrowthTTMYoy": "EPS성장률_TTM_전년비_pct",
    "epsGrowthQuarterlyYoy": "EPS성장률_최근분기_전년비_pct",
    "revenueGrowthTTMYoy": "매출성장률_TTM_전년비_pct",
    "revenueGrowthQuarterlyYoy": "매출성장률_최근분기_전년비_pct",
    "roeTTM": "ROE_TTM_pct",
    "operatingMarginTTM": "영업이익률_TTM_pct",
    "netProfitMarginTTM": "순이익률_TTM_pct",
    "totalDebt/totalEquityQuarterly": "부채_자본_배",
    "currentRatioQuarterly": "유동비율_배",
    "dividendYieldIndicatedAnnual": "배당수익률_pct",
    "beta": "베타",
}

# Finnhub series.quarterly 키 → 이름 (개별 분기 값, 비율은 0~1)
_US_QUARTERLY = {
    "eps": "EPS_USD",
    "salesPerShare": "주당매출_USD",
    "operatingMargin": "영업이익률",
    "netMargin": "순이익률",
}


def summarize_us_financials(metrics: dict, earnings: list, quarters: int = 5) -> dict:
    """Finnhub metric/earnings → 재무 요약 (지표 / 분기 추이 / 실적 서프라이즈)"""
    metric = metrics.get("metric") or {}
    series = (metrics.get("series") or {}).get("quarterly") or {}

    by_period: dict[str, dict] = {}
    for key, label in _US_QUARTERLY.items():
        for point in (series.get(key) or [])[:quarters]:
            if point.get("period"):
                by_period.setdefault(point["period"], {"분기말": point["period"]})[label] = _round(point.get("v"), 4)
    trend = [by_period[p] for p in sorted(by_period, reverse=True)][:quarters]

    surprises = [
        {
            "분기말": e.get("period"),
            "예상EPS_USD": _round(e.get("estimate"), 4),
            "실제EPS_USD": _round(e.get("actual"), 4),
            "서프라이즈_pct": _round(e.get("surprisePercent")),
        }
        for e in (earnings or [])[:4]
    ]

    return {
        "지표": {label: _round(metric.get(key), 4) for key, label in _US_METRICS.items() if metric.get(key) is not None},
        "분기추이_개별분기": trend,
        "실적서프라이즈_최근4분기": surprises,
    }


def filter_us_news(rows: list[dict], limit: int = 20, per_day: int = 5) -> dict:
    """해외뉴스종합 제목 → 날짜별로 고르게 (최신 날짜 먼저, 중복·묶음 기사 제거)

    해외 TR은 종목·거래소를 지정해 조회하므로 받은 기사는 모두 그 종목 기사로 본다
    (국내처럼 시장·업종 기사를 따로 나누지 않는다). 제목은 한국어 번역본이다.
    """
    seen = set()
    by_day: dict[str, list] = {}
    ordered = sorted(rows, key=lambda r: (r.get("data_dt", ""), r.get("data_tm", "")), reverse=True)
    for r in ordered:
        title = _CONTROL_CHARS.sub("", (r.get("title") or "")).strip()
        if not title or _US_NEWS_NOISE.search(title):
            continue
        key = re.sub(r"\s+", "", title)
        if key in seen:
            continue
        seen.add(key)
        day = r.get("data_dt") or ""
        bucket = by_day.setdefault(day, [])
        if len(bucket) < per_day:
            bucket.append({"일자": _news_date(day), "시각": _news_time(r.get("data_tm")),
                           "분류": r.get("class_name"), "제목": title})

    result = []
    for day in sorted(by_day, reverse=True):
        result.extend(by_day[day])
    return {"종목": result[:limit]}
