"""
종목 리뷰 데이터 요약 테스트

실행:
  uv run pytest tests/test_stock_review_summary.py -v

Gemini는 넘겨받은 숫자를 그대로 믿으므로, 요약 단계의 오독 가능 지점을 고정한다.
- KIS 분기 손익은 연 누적이다 → 개별 분기로 바꾸지 않으면 2분기 실적이 두 배로 보인다
- 연간 조회 첫 행은 진행 중인 연도의 누적이다 → 확정 실적과 섞이면 '실적 반토막'으로 읽힌다
- 결산월이 12월이 아닌 종목이 있다 (6월 결산 → 첫 분기는 9월)
- 뉴스 TR은 다른 종목 기사와 시황 묶음을 섞어 준다
"""
import unittest

from app.domain.stock_review import summary as s


def _income(yymm: str, sale, op, net) -> dict:
    return {"stac_yymm": yymm, "sale_account": str(sale), "bsop_prti": str(op), "thtr_ntin": str(net)}


class QuarterlyIncomeTest(unittest.TestCase):
    """연 누적 → 개별 분기"""

    def test_december_fiscal_year(self):
        rows = [
            _income("202606", 300, 30, 20),   # 1~6월 누적
            _income("202603", 100, 10, 5),    # 1분기 = 누적 그대로
            _income("202512", 1000, 90, 60),
            _income("202509", 700, 60, 40),
        ]
        result = s.quarterly_income(rows, fiscal_end_month=12)

        self.assertEqual([r["결산월"] for r in result], ["202606", "202603", "202512", "202509"])
        self.assertEqual(result[0]["매출액_억원"], 200)   # 300 - 100
        self.assertEqual(result[0]["영업이익_억원"], 20)
        self.assertEqual(result[1]["매출액_억원"], 100)   # 첫 분기는 차감 없음
        self.assertEqual(result[2]["매출액_억원"], 300)   # 1000 - 700

    def test_missing_previous_quarter_is_none(self):
        """직전 분기 누적이 없으면 추정하지 않는다"""
        result = s.quarterly_income([_income("202509", 700, 60, 40)], fiscal_end_month=12)
        self.assertIsNone(result[0]["매출액_억원"])

    def test_june_fiscal_year(self):
        """6월 결산: 9월이 첫 분기, 12월은 9월을 뺀다"""
        rows = [
            _income("202612", 250, 25, 15),
            _income("202609", 100, 10, 5),
            _income("202606", 900, 80, 50),
            _income("202603", 600, 50, 30),
        ]
        result = s.quarterly_income(rows, fiscal_end_month=6)
        by = {r["결산월"]: r["매출액_억원"] for r in result}
        self.assertEqual(by["202612"], 150)
        self.assertEqual(by["202609"], 100)
        self.assertEqual(by["202606"], 300)

    def test_october_fiscal_year_wraps_to_january(self):
        """10월 결산: 첫 분기는 이듬해 1월 — 13월로 계산하면 전년 연간 누적을 빼서 큰 음수가 된다"""
        rows = [
            _income("202604", 250, 25, 15),   # 2분기 누적 (11~4월)
            _income("202601", 100, 10, 5),    # 1분기 (11~1월)
            _income("202510", 900, 80, 50),   # 전 회계연도 연간
        ]
        by = {r["결산월"]: r["매출액_억원"] for r in s.quarterly_income(rows, fiscal_end_month=10)}
        self.assertEqual(by["202601"], 100)
        self.assertEqual(by["202604"], 150)

    def test_first_quarter_month_all_fiscal_ends(self):
        expected = {12: 3, 3: 6, 6: 9, 9: 12, 10: 1, 11: 2}
        for fiscal_end, first_q in expected.items():
            self.assertEqual(s._first_quarter_month(fiscal_end), first_q, fiscal_end)

    def test_missing_marker_is_none(self):
        rows = [_income("202603", "99.99", 10, 5)]
        result = s.quarterly_income(rows, fiscal_end_month=12)
        self.assertIsNone(result[0]["매출액_억원"])
        self.assertEqual(result[0]["영업이익_억원"], 10)


class AnnualIncomeTest(unittest.TestCase):

    def test_excludes_in_progress_year(self):
        """연간 조회 첫 행(202606 = 올해 상반기 누적)은 확정 실적이 아니다"""
        rows = [_income("202606", 300, 30, 20), _income("202512", 1000, 90, 60), _income("202412", 900, 80, 50)]
        result = s.annual_income(rows, fiscal_end_month=12)
        self.assertEqual([r["결산월"] for r in result], ["202512", "202412"])


class QuoteStatusTest(unittest.TestCase):

    def test_managed_stock_is_blocked(self):
        quote = {"iscd_stat_cls_code": "51", "mang_issu_cls_code": "Y", "mrkt_warn_cls_code": "00"}
        self.assertEqual(s.blocking_reasons(quote), ["관리종목"])

    def test_normal_stock_not_blocked(self):
        quote = {"iscd_stat_cls_code": "55", "mang_issu_cls_code": "N", "mrkt_warn_cls_code": "00",
                 "temp_stop_yn": "N", "sltr_yn": "N"}
        self.assertEqual(s.blocking_reasons(quote), [])
        self.assertEqual(s.warning_flags(quote), [])

    def test_warnings_are_flags_not_blocks(self):
        quote = {"iscd_stat_cls_code": "55", "mrkt_warn_cls_code": "02", "short_over_yn": "Y"}
        self.assertEqual(s.blocking_reasons(quote), [])
        self.assertEqual(s.warning_flags(quote), ["투자경고", "공매도 과열"])


def _news(code: str, title: str, dt: str = "20261020", tm: str = "120000") -> dict:
    return {"iscd1": code, "hts_pbnt_titl_cntt": title, "data_dt": dt, "data_tm": tm, "dorg": "언론"}


class FilterNewsTest(unittest.TestCase):

    def test_splits_stock_and_market_news(self):
        rows = [
            _news("247540", "에코프로비엠 공매도 과열종목 지정", tm="150000"),
            # 실제 응답: 에코프로비엠 기사인데 086520(에코프로)만 달고 온다 → 제목의 종목명으로 살린다
            _news("086520", "에코프로비엠, 유상증자 청약률 103.25%", tm="140000"),
            _news("003540", "코스피, 3.7% 급락한 5,552 마감", tm="130000"),   # 시장 기사
            _news("247540", "증시요약(2) - 코스닥 마감시황"),                 # 시황 묶음
            _news("222800", "외국계 순매수,도 상위종목(코스닥) 금액기준"),     # 시황 묶음
        ]
        result = s.filter_news(rows, "247540", name="에코프로비엠")
        self.assertEqual([n["제목"] for n in result["종목"]],
                         ["에코프로비엠 공매도 과열종목 지정", "에코프로비엠, 유상증자 청약률 103.25%"])
        self.assertEqual([n["제목"] for n in result["시장_업종"]], ["코스피, 3.7% 급락한 5,552 마감"])

    def test_dedupes_same_title(self):
        rows = [_news("005930", "삼성전자 실적 발표"), _news("005930", "삼성전자  실적 발표", tm="110000")]
        self.assertEqual(len(s.filter_news(rows, "005930")["종목"]), 1)

    def test_code_in_secondary_slot(self):
        row = {"iscd1": "000660", "iscd2": "005930", "hts_pbnt_titl_cntt": "삼전닉스 동반 강세",
               "data_dt": "20261020", "data_tm": "120000"}
        self.assertEqual(len(s.filter_news([row], "005930")["종목"]), 1)

    def test_restores_broken_middle_dot(self):
        """KIS가 가운뎃점을 SUB(0x1A)로 보낸다 — 그대로 넘기면 Gemini 응답에도 깨진 문자가 남는다"""
        rows = [_news("000660", "이찬진 \"솔리다임 중복상장\"…금융위\x1a금감원 의견 같아\x07")]
        title = s.filter_news(rows, "000660")["종목"][0]["제목"]
        self.assertEqual(title, "이찬진 \"솔리다임 중복상장\"…금융위·금감원 의견 같아")

    def test_limits(self):
        rows = [_news("005930", f"삼성전자 기사 {i}", tm=f"{i:06d}") for i in range(5)]
        rows += [_news("000660", f"시장 기사 {i}", tm=f"{i:06d}") for i in range(5)]
        result = s.filter_news(rows, "005930", name="삼성전자", stock_limit=3, market_limit=2)
        self.assertEqual(len(result["종목"]), 3)
        self.assertEqual(len(result["시장_업종"]), 2)


def _bars(closes: list[float]) -> list[dict]:
    return [
        {"STCK_BSOP_DATE": f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
         "STCK_OPRC": c, "STCK_HGPR": c * 1.01, "STCK_LWPR": c * 0.99, "STCK_CLPR": c,
         "ACML_VOL": 1000 + i}
        for i, c in enumerate(closes)
    ]


class SummarizePriceTest(unittest.TestCase):

    def test_uptrend(self):
        closes = [100 + i for i in range(120)]
        result = s.summarize_price(_bars(closes))

        self.assertEqual(result["봉수"], 120)
        self.assertGreater(result["EMA20_괴리_pct"], 0)
        self.assertGreater(result["EMA20_5일기울기_pct"], 0)
        self.assertGreater(result["EMA20_위아래_연속일수"], 0)
        self.assertGreater(result["6개월범위_위치_pct"], 95)
        self.assertGreater(result["수익률_pct"]["6개월"], 0)
        self.assertEqual(len(result["최근20봉"]), 20)
        self.assertEqual(result["최근20봉"][-1]["종가"], 219)

    def test_input_order_does_not_matter(self):
        bars = _bars([100 + i for i in range(60)])
        self.assertEqual(s.summarize_price(bars)["종가"], s.summarize_price(bars[::-1])["종가"])

    def test_short_history_has_no_long_indicators(self):
        result = s.summarize_price(_bars([100 + i for i in range(10)]))
        self.assertIsNone(result["EMA20"])
        self.assertIsNone(result["RSI14"])

    def test_empty(self):
        self.assertEqual(s.summarize_price([]), {"봉수": 0})


class SummarizeInvestorsTest(unittest.TestCase):

    def test_sums_latest_days(self):
        rows = [
            {"stck_bsop_date": f"202610{d:02d}", "frgn_ntby_tr_pbmn": "10", "orgn_ntby_tr_pbmn": "-5",
             "prsn_ntby_tr_pbmn": "-5"}
            for d in range(1, 26)
        ]
        result = s.summarize_investors(rows)
        self.assertEqual(result["누적순매수"]["외국인_5일"], 50)
        self.assertEqual(result["누적순매수"]["기관_20일"], -100)
        self.assertEqual(result["최근10일"][0]["일자"], "20261025")


if __name__ == "__main__":
    unittest.main()
