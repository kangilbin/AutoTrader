"""해외 주문 요청 본문 (foreign_api.place_order_api) — KIS 호출은 모두 모킹

실행: uv run pytest tests/test_overseas_order_request.py -v
SLL_TYPE(판매유형)은 KIS 명세상 매수는 생략, 매도는 "00" 이다.
"""
import unittest
from unittest.mock import patch

from app.core.order import Order
from app.external import foreign_api


async def _place(side: str):
    sent = {}

    async def auth(user_id, db, account_no=None):
        return {"ACCOUNT_NO": "5020627801"}, {"simulation_yn": "Y"}

    async def fetch(method, url, access, json=None, headers=None, **kw):
        sent["json"] = json
        return {"body": {"rt_cd": "0"}}

    order = Order.create(ord_dv=side, itm_no="SGOV", qty=2, unpr=100.42, excg_cd="NYS")
    with patch.object(foreign_api, "_get_user_auth", auth), patch.object(foreign_api, "kis_fetch", fetch), \
            patch.object(foreign_api, "kis_headers", lambda access, tr_id=None, **kw: {}):
        await foreign_api.place_order_api("u", order, None, "5020627801")
    return sent["json"]


class OverseasOrderRequestTest(unittest.IsolatedAsyncioTestCase):
    async def test_sell_sends_sll_type_00(self):
        body = await _place("sell")
        self.assertEqual(body["SLL_TYPE"], "00")

    async def test_buy_omits_sll_type(self):
        body = await _place("buy")
        self.assertNotIn("SLL_TYPE", body)


if __name__ == "__main__":
    unittest.main()
