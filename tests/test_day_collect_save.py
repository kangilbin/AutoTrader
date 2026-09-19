"""
일별 수집 배치 저장 경로 회귀 테스트

실행:
  uv run pytest tests/test_day_collect_save.py -v

운영 장애(2026-09-18 06:40)를 고정한다. 종목 코루틴 5개가 잡의 AsyncSession 하나를
공유한 채 각자 commit 해서, 한쪽 commit 이 await 로 멈춘 사이 다른 쪽이 같은 트랜잭션을
건드렸다 → "Method 'rollback()' can't be called here; method 'commit()' is already in
progress" → 해당 종목들의 일봉이 통째로 누락됐다.

고정하는 것 세 가지.
  1) 수집 코루틴은 저장하지 않는다 — 행만 돌려주고 저장은 잡이 1회로 모은다.
  2) 필수 필드가 빈 응답은 그 종목만 제외된다 (한 건이 그날 전체를 롤백시키지 않도록).
  3) 수집 코루틴은 5개가 동시에 돌아도 세션의 트랜잭션을 건드리지 않는다.
"""
import asyncio
import unittest

from app.domain.swing.trading import auto_swing_batch as batch


def _row(code="005930", **over):
    row = {
        "MRKT_CODE": "J",
        "ST_CODE": code,
        "STCK_BSOP_DATE": "20260918",
        "STCK_OPRC": 70000,
        "STCK_HGPR": 71000,
        "STCK_LWPR": 69500,
        "STCK_CLPR": 70500,
        "ACML_VOL": 1000,
        "FRGN_NTBY_QTY": 0,
        "REG_DT": None,
    }
    row.update(over)
    return row


def _stock(code="005930", mrkt="J"):
    return type("S", (), {"ST_CODE": code, "MRKT_CODE": mrkt})()


class CollectStubs:
    """collect_single_stock 을 실제로 실행하기 위한 최소 패치

    시세 조회와 세션 완료 판정만 대체하고, 행 구성·검증·반환은 프로덕션 코드를 그대로 태운다.
    """

    def __init__(self, domestic=None, overseas=None):
        self.domestic = domestic
        self.overseas = overseas

    def __enter__(self):
        self._orig = (batch.get_target_price, batch.is_today_incomplete,
                      batch.foreign_api.get_target_price)

        async def kr(user_id, code, db, access_data=None):
            await asyncio.sleep(0)
            return self.domestic

        async def us(user_id, code, db, excd=None, access_data=None):
            await asyncio.sleep(0)
            return self.overseas

        batch.get_target_price = kr
        batch.foreign_api.get_target_price = us
        batch.is_today_incomplete = lambda _mrkt: False
        return self

    def __exit__(self, *exc):
        (batch.get_target_price, batch.is_today_incomplete,
         batch.foreign_api.get_target_price) = self._orig
        return False


KR_QUOTE = {
    "stck_bsop_date": "20260918", "stck_oprc": 70000, "stck_hgpr": 71000,
    "stck_lwpr": 69500, "stck_clpr": 70500, "acml_vol": 1000, "frgn_ntby_qty": 0,
}
US_QUOTE = {
    "xymd": "20260918", "open": 210.5, "high": 213.0,
    "low": 209.1, "clos": 212.4, "tvol": 50000,
}


class FakeStockService:
    """save_history_bulk 만 흉내내는 스텁"""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.db = object()              # collect_single_stock 이 시세 조회에 넘기는 값

    async def save_history_bulk(self, rows):
        await asyncio.sleep(0)          # commit 의 네트워크 왕복을 대신한 양보 지점
        self.calls.append(list(rows))
        if self.fail:
            raise RuntimeError("DB 저장 실패")
        return len(rows)


class StoreCollectedRowsTest(unittest.IsolatedAsyncioTestCase):

    async def test_saves_once_for_all_rows(self):
        """종목이 5개여도 저장은 1회, 행은 5건"""
        svc = FakeStockService()
        results = [_row(f"00{i}") for i in range(5)]

        await batch._store_collected_rows(svc, results, "[TEST]")

        self.assertEqual(len(svc.calls), 1, "저장이 종목 수만큼 쪼개졌다")
        self.assertEqual(len(svc.calls[0]), 5)

    async def test_collect_coroutines_never_save(self):
        """종목 코루틴 5개를 동시에 돌려도 아무도 저장하지 않는다

        이것이 장애를 막는 실제 불변식이다. 예전에는 코루틴마다 save_history_bulk →
        commit 을 호출해 공유 세션의 트랜잭션을 동시에 건드렸다.
        저장은 gather 가 끝난 뒤 잡이 1회만 수행해야 한다.
        """
        svc = FakeStockService()
        stocks = [_stock(f"00{i}") for i in range(5)]

        with CollectStubs(domestic=KR_QUOTE):
            results = await asyncio.gather(
                *[batch.collect_single_stock(s, svc, "u1", {"access_token": "T"}) for s in stocks]
            )

        self.assertEqual(svc.calls, [], "수집 코루틴이 직접 저장했다 (공유 세션 동시 commit 경로)")
        self.assertEqual(len(results), 5)
        self.assertTrue(all(isinstance(r, dict) for r in results), f"행을 반환하지 않았다: {results}")

        # 잡이 모아서 1회 저장
        await batch._store_collected_rows(svc, results, "[TEST]")
        self.assertEqual(len(svc.calls), 1)
        self.assertEqual(len(svc.calls[0]), 5)

    async def test_exceptions_and_skips_are_not_saved(self):
        """예외(수집 실패)와 None(스킵)은 저장 대상에서 빠진다"""
        svc = FakeStockService()
        results = [_row("A"), RuntimeError("timeout"), None, _row("B")]

        with self.assertLogs(batch.logger, level="INFO") as logs:
            await batch._store_collected_rows(svc, results, "[TEST]")

        self.assertEqual(len(svc.calls[0]), 2)
        summary = "\n".join(logs.output)
        self.assertIn("수집: 2", summary)
        self.assertIn("저장: 2", summary)
        self.assertIn("실패: 1", summary)
        self.assertIn("건너뜀: 1", summary)

    async def test_no_rows_means_no_save_call(self):
        """저장할 행이 없으면 DB를 건드리지 않는다"""
        svc = FakeStockService()

        await batch._store_collected_rows(svc, [None, RuntimeError("x")], "[TEST]")

        self.assertEqual(svc.calls, [])

    async def test_save_failure_is_logged_not_raised(self):
        """저장 실패는 잡을 중단시키지 않는다 (수집은 이미 끝났고 되돌릴 것이 없다)"""
        svc = FakeStockService(fail=True)

        with self.assertLogs(batch.logger, level="ERROR") as logs:
            await batch._store_collected_rows(svc, [_row("A")], "[TEST]")

        self.assertIn("일별 데이터 저장 실패", "\n".join(logs.output))


class RequiredFieldGuardTest(unittest.TestCase):
    """NOT NULL 컬럼 가드

    저장을 1회로 모은 뒤에는 불량 행 하나가 그날 INSERT 전체를 롤백시킨다.
    수집 단계에서 걸러야 나머지 종목이 살아남는다.
    """

    def test_required_fields_cover_not_null_columns(self):
        from app.domain.stock.entity import StockHistory

        not_null = {
            c.name for c in StockHistory.__table__.columns
            if not c.nullable and c.name not in ("MRKT_CODE", "ST_CODE", "REG_DT")
        }
        self.assertTrue(
            not_null.issubset(set(batch._REQUIRED_HISTORY_FIELDS)),
            f"검증에서 빠진 NOT NULL 컬럼: {not_null - set(batch._REQUIRED_HISTORY_FIELDS)}",
        )



class CollectRejectionTest(unittest.IsolatedAsyncioTestCase):
    """collect_single_stock 의 거부 분기를 프로덕션 코드로 직접 실행한다

    검증 로직을 테스트에서 재구현해 단언하면 상수만 확인될 뿐,
    실제 거부 분기(warning + None 반환)가 동작하는지는 알 수 없다.
    """

    async def _collect(self, stock, **stubs):
        svc = FakeStockService()
        with CollectStubs(**stubs):
            row = await batch.collect_single_stock(stock, svc, "u1", {"access_token": "T"})
        return row, svc

    async def test_missing_field_is_rejected_with_warning(self):
        """필수 필드가 None·빈 문자열이면 그 종목만 제외된다 (KIS 는 빈 문자열을 준다)"""
        for bad in (None, ""):
            with self.subTest(value=bad):
                quote = {**KR_QUOTE, "stck_clpr": bad}

                with self.assertLogs(batch.logger, level="WARNING") as logs:
                    row, svc = await self._collect(_stock(), domestic=quote)

                self.assertIsNone(row, f"값={bad!r} 인 행이 저장 대상으로 통과했다")
                self.assertIn("STCK_CLPR", "\n".join(logs.output))
                self.assertEqual(svc.calls, [], "거부된 종목이 저장됐다")

    async def test_empty_response_is_rejected_with_warning(self):
        """빈 응답도 침묵하지 않는다 (G-01)"""
        with self.assertLogs(batch.logger, level="WARNING") as logs:
            row, _ = await self._collect(_stock("017670"), domestic=None)

        self.assertIsNone(row)
        self.assertIn("017670", "\n".join(logs.output))
        self.assertIn("응답 없음", "\n".join(logs.output))

    async def test_incomplete_session_skips_before_quote(self):
        """세션 미완료면 시세를 조회하지도 않고 건너뛴다 (완성봉 불변식)"""
        svc = FakeStockService()
        orig = batch.is_today_incomplete
        batch.is_today_incomplete = lambda _mrkt: True
        try:
            row = await batch.collect_single_stock(_stock(), svc, "u1", {"access_token": "T"})
        finally:
            batch.is_today_incomplete = orig

        self.assertIsNone(row)
        self.assertEqual(svc.calls, [])


class OverseasCollectTest(unittest.IsolatedAsyncioTestCase):
    """해외 분기 커버 (foreign_api 경로 + 필드명이 국내와 다름)"""

    async def test_overseas_row_is_built_from_foreign_fields(self):
        svc = FakeStockService()

        with CollectStubs(overseas=US_QUOTE):
            row = await batch.collect_single_stock(
                _stock("AMD", mrkt="NAS"), svc, "u1", {"access_token": "T"}
            )

        self.assertIsNotNone(row, "해외 종목이 저장 대상에서 빠졌다")
        self.assertEqual(row["MRKT_CODE"], "NAS")
        self.assertEqual(row["STCK_BSOP_DATE"], "20260918")   # xymd
        self.assertEqual(row["STCK_CLPR"], 212.4)             # clos
        self.assertEqual(row["ACML_VOL"], 50000)              # tvol
        self.assertEqual(svc.calls, [], "해외 경로도 직접 저장하면 안 된다")

    async def test_overseas_missing_field_is_rejected(self):
        """해외도 동일한 가드가 걸린다"""
        svc = FakeStockService()

        with CollectStubs(overseas={**US_QUOTE, "tvol": None}):
            row = await batch.collect_single_stock(
                _stock("AMD", mrkt="NAS"), svc, "u1", {"access_token": "T"}
            )

        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
