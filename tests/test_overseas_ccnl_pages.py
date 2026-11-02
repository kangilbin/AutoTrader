"""해외 주문체결내역 연속조회 (foreign_api.get_inquire_ccnl_obj) — KIS 호출은 모두 모킹

실행: uv run pytest tests/test_overseas_ccnl_pages.py -v
모의는 1페이지 15건을 오래된 순으로 준다. 하루 주문이 15건을 넘으면 방금 낸 주문이 1페이지 밖이라
체결 확인이 실패했다 (2026-11-03 모의 미국 풀 테스트).
"""
import unittest
from unittest.mock import patch

from app.external import foreign_api


def _page(orders, more, nk="NK"):
    body = {"rt_cd": "0", "output": [{"odno": str(o)} for o in orders], "ctx_area_fk200": "", "ctx_area_nk200": nk if more else ""}
    return {"body": body, "header": {"tr_cont": "M" if more else "D"}}


class Fetcher:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    async def __call__(self, method, url, access, params=None, headers=None, **kw):
        self.calls.append((dict(params), dict(headers or {})))
        return self.pages.pop(0)


async def _run(fetcher):
    async def auth(user_id, db, account_no=None):
        return {"ACCOUNT_NO": "5020627801"}, {"simulation_yn": "Y"}
    with patch.object(foreign_api, "_get_user_auth", auth), patch.object(foreign_api, "kis_fetch", fetcher), \
            patch.object(foreign_api, "kis_headers", lambda access, tr_id=None, extra=None, **kw: dict(extra or {})):
        return await foreign_api.get_inquire_ccnl_obj("u", None, "NYS", account_no="5020627801")


class CcnlPagesTest(unittest.IsolatedAsyncioTestCase):
    async def test_reads_all_pages_and_merges_orders(self):
        fetcher = Fetcher([_page([1, 2], True), _page([3], False)])
        body = await _run(fetcher)
        self.assertEqual([o["odno"] for o in body["output"]], ["1", "2", "3"])
        self.assertEqual(len(fetcher.calls), 2)

    async def test_next_page_sends_continuation_key_and_header(self):
        fetcher = Fetcher([_page([1], True, nk="KEY1"), _page([2], False)])
        await _run(fetcher)
        first, second = fetcher.calls
        self.assertEqual(first[0]["CTX_AREA_NK200"], "")
        self.assertEqual(second[0]["CTX_AREA_NK200"], "KEY1")
        self.assertEqual(second[1].get("tr_cont"), "N")
        self.assertNotIn("tr_cont", first[1])

    async def test_single_page_makes_one_call(self):
        fetcher = Fetcher([_page([1], False)])
        body = await _run(fetcher)
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual(len(body["output"]), 1)

    async def test_page_limit_stops_endless_continuation(self):
        fetcher = Fetcher([_page([i], True) for i in range(foreign_api.CCNL_MAX_PAGES + 5)])
        await _run(fetcher)
        self.assertEqual(len(fetcher.calls), foreign_api.CCNL_MAX_PAGES)

    async def test_error_response_stops_paging(self):
        err = {"body": {"rt_cd": "1", "msg1": "오류"}, "header": {"tr_cont": "M"}}
        fetcher = Fetcher([err, _page([1], False)])
        body = await _run(fetcher)
        self.assertEqual(body["rt_cd"], "1")
        self.assertEqual(len(fetcher.calls), 1)


if __name__ == "__main__":
    unittest.main()
