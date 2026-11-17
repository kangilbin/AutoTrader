"""
종목 리뷰 데이터 요약 테스트

실행:
  uv run pytest tests/test_stock_review_summary.py -v

Gemini는 넘겨받은 숫자를 그대로 믿으므로, 요약 단계의 오독 가능 지점을 고정한다.
- DART 분기 손익은 thstrm_amount 가 단독 분기, thstrm_add_amount 가 누적이다 → 헷갈리면 실적이 두 배로 보인다
- 4분기는 보고서가 없다 → 사업보고서 연간 − 3분기 누적으로 구하고, 3분기를 모르면 만들지 않는다
- 결산월이 12월이 아닌 종목이 있다 → 달력이 아니라 계정 행의 기간 문자열에서 읽는다
- 뉴스 TR은 다른 종목 기사와 시황 묶음을 섞어 준다
"""
import unittest

from app.domain.stock_review import summary as s


def _is(name: str, amount, add=None, dt="2026.01.01 ~ 2026.09.30", sj="IS") -> dict:
    """DART 주요계정 행 — 분기·반기 보고서의 thstrm_amount 는 단독 분기, thstrm_add_amount 는 연 누적"""
    return {"sj_div": sj, "account_nm": name, "thstrm_amount": f"{amount:,}" if amount is not None else "-",
            "thstrm_add_amount": f"{add:,}" if add is not None else None, "thstrm_dt": dt}


def _report(year: int, code: str, sales, op, net, add=(None, None, None), dt="2026.01.01 ~ 2026.09.30") -> dict:
    rows = [_is("매출액", sales, add[0], dt), _is("영업이익", op, add[1], dt),
            _is("당기순이익(손실)", net, add[2], dt), _is("당기순이익(손실)", net, add[2], dt)]
    return {"year": year, "reprt_code": code, "rows": rows}


EOK = 100_000_000


class DartQuarterlyIncomeTest(unittest.TestCase):
    """DART 보고서 → 개별 분기 손익"""

    def test_quarter_and_half_reports_use_standalone_amount(self):
        """thstrm_amount 가 이미 단독 분기다 — thstrm_add_amount(누적)를 쓰면 실적이 부풀어 보인다"""
        reports = [_report(2026, "11014", 300 * EOK, 30 * EOK, 20 * EOK, add=(900 * EOK, 90 * EOK, 60 * EOK))]
        result = s.quarterly_income(reports)
        self.assertEqual(result[0]["매출액_억원"], 300.0)
        self.assertEqual(result[0]["결산월"], "2026-09")
        self.assertEqual(result[0]["영업이익률_pct"], 10.0)

    def test_fourth_quarter_is_annual_minus_third_quarter_cumulative(self):
        reports = [
            _report(2025, "11011", 1000 * EOK, 100 * EOK, 60 * EOK, dt="2025.01.01 ~ 2025.12.31"),
            _report(2025, "11014", 250 * EOK, 25 * EOK, 15 * EOK, add=(700 * EOK, 70 * EOK, 40 * EOK),
                    dt="2025.01.01 ~ 2025.09.30"),
        ]
        result = s.quarterly_income(reports)
        self.assertEqual(result[0]["결산월"], "2025-12")
        self.assertEqual(result[0]["매출액_억원"], 300.0)      # 1000 - 700
        self.assertEqual(result[0]["당기순이익_억원"], 20.0)    # 60 - 40
        self.assertEqual(result[1]["매출액_억원"], 250.0)

    def test_fourth_quarter_skipped_without_third_quarter_report(self):
        """3분기 누적을 모르면 4분기를 계산할 수 없다 — 연간 값을 4분기 값으로 내보내면 안 된다"""
        reports = [_report(2025, "11011", 1000 * EOK, 100 * EOK, 60 * EOK, dt="2025.01.01 ~ 2025.12.31")]
        self.assertEqual(s.quarterly_income(reports), [])

    def test_non_december_fiscal_year_uses_period_end_from_dates(self):
        """6월 결산 회사의 3분기 보고서 = 3월말 — 달력 분기로 가정하지 않고 기간 문자열에서 읽는다"""
        reports = [_report(2026, "11014", 100 * EOK, 10 * EOK, 5 * EOK, dt="2025.07.01 ~ 2026.03.31")]
        self.assertEqual(s.quarterly_income(reports)[0]["결산월"], "2026-03")

    def test_missing_operating_income_is_none(self):
        """금융업 등 영업이익 계정이 없는 회사 — 지어내지 않고 None"""
        rep = {"year": 2026, "reprt_code": "11013",
               "rows": [_is("매출액", 100 * EOK, dt="2026.01.01 ~ 2026.03.31")]}
        item = s.quarterly_income([rep])[0]
        self.assertIsNone(item["영업이익_억원"])
        self.assertIsNone(item["영업이익률_pct"])

    def test_limit(self):
        reports = [_report(2026, "11014", EOK, EOK, EOK)] * 7
        self.assertEqual(len(s.quarterly_income(reports, limit=5)), 5)


class DartAnnualIncomeTest(unittest.TestCase):

    def _annual(self):
        """실제 응답처럼 한 계정 행에 당기·전기·전전기가 같이 온다"""
        row = {"sj_div": "IS", "account_nm": "매출액",
               "thstrm_amount": f"{1500 * EOK:,}", "thstrm_dt": "2025.01.01 ~ 2025.12.31",
               "frmtrm_amount": f"{700 * EOK:,}", "frmtrm_dt": "2024.01.01 ~ 2024.12.31",
               "bfefrmtrm_amount": f"{500 * EOK:,}", "bfefrmtrm_dt": "2023.01.01 ~ 2023.12.31"}
        return {"year": 2025, "reprt_code": "11011", "rows": [row]}

    def test_three_years_from_one_annual_report(self):
        result = s.annual_income([self._annual()])
        self.assertEqual([r["결산월"] for r in result], ["2025-12", "2024-12", "2023-12"])
        self.assertEqual([r["매출액_억원"] for r in result], [1500.0, 700.0, 500.0])

    def test_no_annual_report(self):
        """분기 보고서의 누적 행은 확정 연간 실적이 아니다"""
        self.assertEqual(s.annual_income([_report(2026, "11014", EOK, EOK, EOK)]), [])


class DartIndicatorsTest(unittest.TestCase):

    def test_picks_by_code_and_blanks_broken_values(self):
        rows = [
            {"idx_code": "M211550", "idx_nm": "ROE", "idx_val": "66.495"},
            {"idx_code": "M221100", "idx_nm": "부채비율", "idx_val": "91.099"},
            {"idx_code": "M221000", "idx_nm": "자기자본비율", "idx_val": "#########"},   # 실제로 내려오는 깨진 값
            {"idx_code": "M231000", "idx_nm": "매출액증가율(YoY)"},                      # idx_val 키 자체가 없는 행
            {"idx_code": "M212600", "idx_nm": "납입자본이익률", "idx_val": "9477.5"},    # 고르지 않는 지표
        ]
        result = s.indicators(rows)
        self.assertEqual(result["수익성"]["ROE_pct"], 66.5)
        self.assertEqual(result["안정성"]["부채비율_pct"], 91.1)
        self.assertIsNone(result["안정성"]["자기자본비율_pct"])
        self.assertIsNone(result["성장성"]["매출액증가율_pct"])
        self.assertNotIn("납입자본이익률", str(result))


class SummarizeFinancialsTest(unittest.TestCase):

    def test_no_reports_leaves_reason_for_model(self):
        """신규 상장 등 보고서가 없으면 빈 재무를 '좋은 실적'처럼 보내지 않고 사유를 남긴다"""
        result = s.summarize_financials({"reports": [], "indicators": []})
        self.assertIn("조회실패", result)
        self.assertNotIn("분기실적_개별분기", result)

    def test_shape(self):
        data = {"reports": [_report(2026, "11014", 300 * EOK, 30 * EOK, 20 * EOK)], "indicators": []}
        result = s.summarize_financials(data)
        self.assertEqual(result["기준보고서"], "2026년 3분기 (2026-09 결산)")
        self.assertEqual(set(result), {"기준보고서", "분기실적_개별분기", "연간실적_확정", "재무지표_최근보고서_연초누적기준"})


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

    def test_spreads_across_days(self):
        """기사가 많은 날이 있어도 하루 상한만큼만 담아 며칠 전 기사가 남는다"""
        rows = [_news("005930", f"삼성전자 오늘 기사 {i}", dt="20261020", tm=f"{i:06d}") for i in range(20)]
        rows += [_news("005930", "삼성전자 3분기 실적 발표", dt="20261016")]
        result = s.filter_news(rows, "005930", name="삼성전자", stock_per_day=5)
        titles = [n["제목"] for n in result["종목"]]
        self.assertEqual(len(titles), 6)
        self.assertIn("삼성전자 3분기 실적 발표", titles)

    def test_disclosure_first_within_day(self):
        """같은 날 기사가 상한을 넘으면 공시를 먼저 담는다"""
        rows = [_news("247540", f"에코프로비엠 기사 {i}", tm=f"1{i}0000") for i in range(5)]
        disclosure = _news("247540", "(주)에코프로비엠 유상증자 결정", tm="080000")
        disclosure["dorg"] = "코스닥 공시"
        rows.append(disclosure)
        titles = [n["제목"] for n in s.filter_news(rows, "247540", name="에코프로비엠", stock_per_day=3)["종목"]]
        self.assertEqual(titles[0], "(주)에코프로비엠 유상증자 결정")
        self.assertEqual(len(titles), 3)

    def test_readable_date_and_time(self):
        item = s.filter_news([_news("005930", "삼성전자 기사", dt="20261020", tm="184030")], "005930")["종목"][0]
        self.assertEqual(item["일자"], "2026-10-20 (화)")
        self.assertEqual(item["시각"], "18:40")

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
    """일반 투자 관점 요약 — 매매 전략 지표(EMA20·OBV·ADX)는 넘기지 않는다"""

    def test_uptrend(self):
        closes = [100 + i for i in range(130)]
        result = s.summarize_price(_bars(closes))

        self.assertEqual(result["봉수"], 130)
        self.assertGreater(result["수익률_pct"]["6개월"], 0)
        self.assertGreater(result["6개월범위_위치_pct"], 95)
        self.assertGreater(result["이동평균_대비_pct"]["20일"], 0)
        self.assertGreater(result["이동평균_대비_pct"]["120일"], 0)
        self.assertEqual(result["6개월_최대낙폭_pct"], 0.0)
        self.assertEqual(len(result["최근10일"]), 10)
        self.assertEqual(result["최근10일"][-1]["종가"], 229)

    def test_no_strategy_indicators(self):
        """전략 지표가 섞이면 모델이 투자 판단을 전략 진입 조건으로 설명한다"""
        result = s.summarize_price(_bars([100 + i for i in range(130)]))
        keys = " ".join(result)
        for banned in ("EMA", "OBV", "ADX", "DI", "ATR"):
            self.assertNotIn(banned, keys)

    def test_max_drawdown_and_monthly(self):
        closes = [100] * 30 + [80] * 30 + [120] * 30
        result = s.summarize_price(_bars(closes))
        self.assertEqual(result["6개월_최대낙폭_pct"], -20.0)
        monthly = result["월별흐름"]
        self.assertIsNone(monthly[0]["등락_pct"])
        self.assertEqual(monthly[-1]["월말종가"], 120)

    def test_input_order_does_not_matter(self):
        bars = _bars([100 + i for i in range(60)])
        self.assertEqual(s.summarize_price(bars)["종가"], s.summarize_price(bars[::-1])["종가"])

    def test_short_history(self):
        result = s.summarize_price(_bars([100 + i for i in range(10)]))
        self.assertIsNone(result["RSI14"])
        self.assertIsNone(result["이동평균_대비_pct"]["20일"])

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


class UsSummaryTest(unittest.TestCase):
    """미국: KIS 해외 현재가·뉴스 + Finnhub 재무"""

    def test_blocking_only_when_not_tradable(self):
        self.assertEqual(s.us_blocking_reasons({"e_ordyn": "매매 가능"}), [])
        self.assertEqual(s.us_blocking_reasons({"e_ordyn": "매매 불가"}), ["매매 불가(매매 불가)"])

    def test_quote(self):
        q = s.summarize_us_quote({"last": "110", "base": "100", "perx": "38.79", "tomv": "4938239454000",
                                  "h52p": "220", "l52p": "55", "e_icod": "컴퓨터전자장비/기기"})
        self.assertEqual(q["전일대비_pct"], 10.0)
        self.assertEqual(q["PER"], 38.79)
        self.assertEqual(q["52주최고_대비_pct"], -50.0)
        self.assertEqual(q["52주최저_대비_pct"], 100.0)

    def test_financials(self):
        metrics = {
            "metric": {"peTTM": 38.53, "roeTTM": 137.18, "totalDebt/totalEquityQuarterly": 0.78, "unknownKey": 1},
            "series": {"quarterly": {
                "eps": [{"period": "2026-06-27", "v": 2.0244}, {"period": "2026-03-28", "v": 2.0086}],
                "netMargin": [{"period": "2026-06-27", "v": 0.2723}],
            }},
        }
        earnings = [{"period": "2026-06-30", "estimate": 1.9271, "actual": 1.91, "surprisePercent": -0.8873}]
        f = s.summarize_us_financials(metrics, earnings)
        self.assertEqual(f["지표"]["PER_TTM"], 38.53)
        self.assertEqual(f["지표"]["부채_자본_배"], 0.78)
        self.assertNotIn("unknownKey", f["지표"])
        self.assertEqual(f["분기추이_개별분기"][0], {"분기말": "2026-06-27", "EPS_USD": 2.0244, "순이익률": 0.2723})
        self.assertEqual(f["실적서프라이즈_최근4분기"][0]["서프라이즈_pct"], -0.89)

    def test_financials_empty(self):
        f = s.summarize_us_financials({}, [])
        self.assertEqual(f, {"지표": {}, "분기추이_개별분기": [], "실적서프라이즈_최근4분기": []})

    def test_news_spread_and_noise(self):
        rows = [{"news_key": f"k{i}", "data_dt": "20261020", "data_tm": f"1{i}0000", "title": f"애플 기사 {i}"}
                for i in range(8)]
        rows += [{"news_key": "old", "data_dt": "20261016", "data_tm": "151502", "title": "애플, 메타와 협력"},
                 {"news_key": "noise", "data_dt": "20261020", "data_tm": "210709",
                  "title": "[개장 전 특징주] 스틸 다이내믹스, EPS 기대 못 미치며 하락"}]
        news = s.filter_us_news(rows, per_day=5)["종목"]
        titles = [n["제목"] for n in news]
        self.assertEqual(len(titles), 6)
        self.assertIn("애플, 메타와 협력", titles)
        self.assertNotIn("[개장 전 특징주] 스틸 다이내믹스, EPS 기대 못 미치며 하락", titles)
        self.assertEqual(news[-1]["일자"], "2026-10-16 (금)")


if __name__ == "__main__":
    unittest.main()
