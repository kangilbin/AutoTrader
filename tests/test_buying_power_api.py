"""매수가능조회 API 래퍼 (kis_api.get_buying_power / foreign_api.get_buying_power) — KIS 호출은 모두 모킹

실행: uv run pytest tests/test_buying_power_api.py -v
모의 계좌 실측 응답(2026-11-01)을 그대로 쓴다.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.external import foreign_api, kis_api

KR_OUT = {"ord_psbl_cash": "92192036", "psbl_qty_calc_unpr": "301500", "nrcvb_buy_amt": "92642163",
          "nrcvb_buy_qty": "305", "max_buy_amt": "231638282"}   # 모의 실측 (ruse_psbl_amt 는 0 이라 응답에서 빠져 있다)
US_OUT = {"tr_crcy_cd": "USD", "ord_psbl_frcr_amt": "86211.80", "sll_ruse_psbl_amt": "16650.29",
          "ovrs_ord_psbl_amt": "86211.80", "frcr_ord_psbl_amt1": "258723.995469"}


class Harness:
    """_get_user_auth · kis_fetch · kis_headers 를 대체하고 요청 내용을 기록한다"""

    def __init__(self, module, body, sim="Y"):
        self.module, self.body, self.sim = module, body, sim
        self.requests, self.tr_ids = [], []

    def __enter__(self):
        async def auth(user_id, db, account_no=None):
            return {"ACCOUNT_NO": "5020627801"}, {"simulation_yn": self.sim}

        async def fetch(method, url, access, params=None, headers=None, **kw):
            self.requests.append((method, url, params))
            if isinstance(self.body, Exception):
                raise self.body
            return {"body": self.body}

        def headers(access, tr_id=None, **kw):
            self.tr_ids.append(tr_id)
            return {}
        self.patches = [patch.object(self.module, "_get_user_auth", auth), patch.object(self.module, "kis_fetch", fetch),
                        patch.object(self.module, "kis_headers", headers)]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in self.patches:
            p.stop()


class DomesticTest(unittest.IsolatedAsyncioTestCase):
    async def test_cash_is_the_no_margin_buy_amount_per_the_kis_spec(self):
        """KIS 명세: 미수를 쓰지 않으면 nrcvb_buy_amt 를 본다 — max_buy_amt(미수 포함 2.3억)·ord_psbl_cash 가 아니다"""
        with Harness(kis_api, {"rt_cd": "0", "output": KR_OUT}) as h:
            r = await kis_api.get_buying_power("u", None, "005930", account_no="5020627801")
        self.assertEqual(r["cash"], 92_642_163.0)
        self.assertEqual((r["ord_psbl_cash"], r["ruse_psbl_amt"]), (92_192_036.0, 0.0))
        method, url, params = h.requests[0]
        self.assertTrue(url.endswith("/uapi/domestic-stock/v1/trading/inquire-psbl-order"))
        self.assertEqual((params["CANO"], params["ACNT_PRDT_CD"], params["PDNO"], params["ORD_DVSN"]),
                         ("50206278", "01", "005930", "01"))

    async def test_sale_proceeds_are_counted_after_a_sale(self):
        """매도 직후: ord_psbl_cash 는 그대로이고 ruse_psbl_amt(매도대금 재사용분)와 nrcvb_buy_amt 가 늘어난 응답 —
        ord_psbl_cash 로 판단하면 '안 늘었다'고 오판해 살 수 있는데도 중단한다"""
        after_sale = {**KR_OUT, "ruse_psbl_amt": "5000000", "nrcvb_buy_amt": "97642163"}
        with Harness(kis_api, {"rt_cd": "0", "output": after_sale}):
            r = await kis_api.get_buying_power("u", None)
        self.assertEqual(r["cash"], 97_642_163.0)
        self.assertEqual(r["ord_psbl_cash"], 92_192_036.0)

    async def test_falls_back_to_cash_plus_reusable_when_no_margin_amount_is_missing(self):
        out = {"ord_psbl_cash": "1000000", "ruse_psbl_amt": "300000"}
        with Harness(kis_api, {"rt_cd": "0", "output": out}):
            r = await kis_api.get_buying_power("u", None)
        self.assertEqual(r["cash"], 1_300_000.0)

    async def test_mock_and_real_use_different_tr_ids(self):
        with Harness(kis_api, {"rt_cd": "0", "output": KR_OUT}, sim="Y") as h:
            await kis_api.get_buying_power("u", None)
        self.assertEqual(h.tr_ids, ["VTTC8908R"])
        with Harness(kis_api, {"rt_cd": "0", "output": KR_OUT}, sim="N") as h:
            await kis_api.get_buying_power("u", None)
        self.assertEqual(h.tr_ids, ["TTTC8908R"])

    async def test_failures_return_none_instead_of_raising(self):
        for body in ({"rt_cd": "1", "msg1": "오류", "output": {}}, {"rt_cd": "0", "output": {}},
                     {"rt_cd": "0", "output": {"ord_psbl_cash": ""}}, RuntimeError("timeout")):
            with Harness(kis_api, body):
                self.assertIsNone(await kis_api.get_buying_power("u", None), body)


class OverseasTest(unittest.IsolatedAsyncioTestCase):
    async def test_cash_is_orderable_foreign_currency_and_reuse_is_reported(self):
        with Harness(foreign_api, {"rt_cd": "0", "output": US_OUT}) as h:
            r = await foreign_api.get_buying_power("u", None, "SGOV", "NYS", 100.73, account_no="5020627801")
        self.assertAlmostEqual(r["cash"], 86_211.80)         # 통합증거금 환산 매수력(258,723)은 세지 않는다
        self.assertAlmostEqual(r["reuse"], 16_650.29)
        _, url, params = h.requests[0]
        self.assertTrue(url.endswith("/uapi/overseas-stock/v1/trading/inquire-psamount"))
        self.assertEqual((params["OVRS_EXCG_CD"], params["ITEM_CD"], params["OVRS_ORD_UNPR"]), ("NYSE", "SGOV", "100.73"))
        self.assertEqual(set(params), {"CANO", "ACNT_PRDT_CD", "OVRS_EXCG_CD", "OVRS_ORD_UNPR", "ITEM_CD"}, "명세의 필수 항목 5개 그대로")
        self.assertEqual((params["CANO"], params["ACNT_PRDT_CD"]), ("50206278", "01"))

    async def test_unit_price_is_compact_fixed_point(self):
        """증권사는 소수 8자리를 채운 값(100.00000000)을 INVALID INPUT_FILED_SIZE 로 거절한다 (모의 실측) — 뒤의 0 을 뗀다.
        지수 표기(1e-05)도 만들지 않는다"""
        for price, expected in ((100.0, "100"), (100.73, "100.73"), (0.00001, "0.00001"), (12345678.5, "12345678.5"),
                                (1.0, "1"), (0.0, "0")):
            with Harness(foreign_api, {"rt_cd": "0", "output": US_OUT}) as h:
                await foreign_api.get_buying_power("u", None, "SGOV", "NYS", price)
            sent = h.requests[0][2]["OVRS_ORD_UNPR"]
            self.assertEqual(sent, expected)
            self.assertNotIn("e", sent.lower())
            self.assertFalse(sent.endswith("0") and "." in sent, "소수부 끝에 0 이 남지 않는다")

    async def test_takes_the_smaller_of_the_two_orderable_fields(self):
        out = {**US_OUT, "ord_psbl_frcr_amt": "90000", "ovrs_ord_psbl_amt": "80000"}
        with Harness(foreign_api, {"rt_cd": "0", "output": out}):
            r = await foreign_api.get_buying_power("u", None, "SGOV", "NYS")
        self.assertEqual(r["cash"], 80_000.0)

    async def test_mock_and_real_use_different_tr_ids(self):
        with Harness(foreign_api, {"rt_cd": "0", "output": US_OUT}, sim="Y") as h:
            await foreign_api.get_buying_power("u", None, "SGOV", "NYS")
        self.assertEqual(h.tr_ids, ["VTTS3007R"])
        with Harness(foreign_api, {"rt_cd": "0", "output": US_OUT}, sim="N") as h:
            await foreign_api.get_buying_power("u", None, "SGOV", "NYS")
        self.assertEqual(h.tr_ids, ["TTTS3007R"])

    async def test_failures_return_none_instead_of_raising(self):
        for body in ({"rt_cd": "1", "msg1": "오류", "output": {}}, {"rt_cd": "0", "output": {}}, RuntimeError("timeout")):
            with Harness(foreign_api, body):
                self.assertIsNone(await foreign_api.get_buying_power("u", None, "SGOV", "NYS"), body)


if __name__ == "__main__":
    unittest.main()
