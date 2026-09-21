"""백테스트 응답 회귀 테스트 (직렬화 + 구간 표기)

실행:
  uv run pytest tests/test_backtest_response.py -v

문제: 평가 구간(최근 2년) 첫 봉이 데이터 시작점과 겹치면 — 즉 보유 데이터가
2년 미만이면 — EMA20 워밍업 19봉이 차트 데이터에 NaN 으로 포함된다.
Starlette 의 JSONResponse 는 allow_nan=False 로 덤프하므로 500 이 난다.

`Series.where(cond, None)` 가드가 있었지만 pandas 는 float 컬럼에서 None 을
다시 NaN 으로 캐스팅하므로 실질적으로 아무 일도 하지 않았다. 그래서 이 테스트는
"None 으로 바뀌었는지"가 아니라 "실제 직렬화가 되는지"를 검증한다.
"""
import json
import unittest
from datetime import datetime

import pandas as pd
from dateutil.relativedelta import relativedelta

from app.domain.swing.backtest.strategy_factory import StrategyFactory


def _bars(n: int) -> pd.DataFrame:
    """거래일 n개 분량의 OHLCV (최신 봉이 오늘 → 전부 평가 구간 안에 들어온다)"""
    end = datetime.now()
    rows = []
    for i in range(n):
        close = 10_000 + (i % 40) * 120          # 상승·하락이 섞이도록 톱니 모양
        rows.append({
            "STCK_BSOP_DATE": (end - relativedelta(days=n - 1 - i)).strftime("%Y%m%d"),
            "STCK_OPRC": close - 50,
            "STCK_HGPR": close + 120,
            "STCK_LWPR": close - 120,
            "STCK_CLPR": close,
            "ACML_VOL": 1_000_000 + i * 1_000,
        })
    return pd.DataFrame(rows)


def _params() -> dict:
    return {
        "st_code": "005930",
        "swing_type": "S",
        "short_term": 5,
        "medium_term": 20,
        "long_term": 60,
        "init_amount": 10_000_000,
        "eval_start": datetime.now() - relativedelta(years=2),
    }


class EvalRangeReportingTest(unittest.TestCase):
    """응답의 start_date 는 실제 평가된 첫 봉이어야 한다

    문제: 평가 시작일(2년 전) 요청값을 그대로 응답에 넣고 있었다. 보유 데이터가
    1년뿐이어도 응답은 "2년 전부터 백테스트했다"고 말하므로, 1년치 결과를
    2년 성과로 읽게 된다. 같은 종목도 구간이 바뀌면 수익률 부호가 뒤집힌다.
    """

    def test_short_history_reports_actual_first_bar(self):
        """데이터가 평가 구간보다 짧으면 데이터 시작일을 보고한다"""
        bars = _bars(150)
        # compute() 는 _prepare_data 에서 입력 DataFrame 의 날짜 컬럼을 제자리에서
        # datetime 으로 바꾼다. 기댓값은 호출 전에 읽어둬야 한다.
        expected = datetime.strptime(bars["STCK_BSOP_DATE"].iloc[0], "%Y%m%d")

        result = StrategyFactory.get_strategy("S").compute(bars, _params())
        self.assertEqual(result["start_date"], expected.strftime("%Y-%m-%d"))

    def test_long_history_reports_requested_eval_start(self):
        """데이터가 충분하면 요청한 평가 시작일 이후 첫 봉을 보고한다"""
        params = _params()
        result = StrategyFactory.get_strategy("S").compute(_bars(900), params)

        self.assertGreaterEqual(
            datetime.strptime(result["start_date"], "%Y-%m-%d"),
            params["eval_start"].replace(hour=0, minute=0, second=0, microsecond=0),
        )
        # 요청 시작일에 바짝 붙어야 한다 (봉 하나 차이 이상 벌어지면 구간이 잘린 것)
        gap = datetime.strptime(result["start_date"], "%Y-%m-%d") - params["eval_start"]
        self.assertLessEqual(gap.days, 1)

    def test_start_date_matches_first_chart_bar(self):
        """start_date 와 차트 첫 봉이 어긋나면 안 된다"""
        result = StrategyFactory.get_strategy("S").compute(_bars(150), _params())

        first_chart_bar = result["price_history"][0]["STCK_BSOP_DATE"]
        self.assertEqual(
            result["start_date"],
            datetime.strptime(first_chart_bar, "%Y%m%d").strftime("%Y-%m-%d"),
        )


class ChartDataSerializationTest(unittest.TestCase):
    """차트 데이터에 NaN 이 남으면 응답 자체가 500 이 된다"""

    def test_warmup_nan_is_serializable(self):
        """평가 구간에 EMA 워밍업이 포함돼도 JSON 으로 나갈 수 있어야 한다"""
        result = StrategyFactory.get_strategy("S").compute(_bars(150), _params())

        # allow_nan=False 는 Starlette JSONResponse 와 동일한 설정이다
        json.dumps(result, default=str, allow_nan=False)

    def test_warmup_bars_are_null_not_dropped(self):
        """워밍업 구간은 행을 지우지 않고 null 로 남긴다 (날짜 축 정렬 유지)"""
        result = StrategyFactory.get_strategy("S").compute(_bars(150), _params())

        ema_history = result["ema20_history"]
        self.assertEqual(len(ema_history), len(result["price_history"]))
        self.assertIsNone(ema_history[0]["ema20"])
        self.assertIsNotNone(ema_history[-1]["ema20"])

    def test_full_history_has_no_nulls(self):
        """워밍업이 평가 구간 밖인 정상 케이스는 값이 모두 채워진다

        _bars 는 달력일 기준이므로 2년(평가 구간)을 넘기려면 900일 분량이 필요하다.
        """
        result = StrategyFactory.get_strategy("S").compute(_bars(900), _params())

        json.dumps(result, default=str, allow_nan=False)
        self.assertTrue(all(r["ema20"] is not None for r in result["ema20_history"]))


if __name__ == "__main__":
    unittest.main()
