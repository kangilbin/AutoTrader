"""
스윙 소유권 검증 회귀 테스트

실행:
  uv run pytest tests/test_swing_ownership.py -v

SWING_TRADE에는 USER_ID가 없다. 소유자는 ACCOUNT_NO로 ACCOUNT를 조인해야 나오는데,
그 조인을 빠뜨리면 스윙 ID(순번)만 아는 사용자가 남의 자동매매를 조회·수정·삭제할 수
있다. 조회는 보유 종목과 투자금이, 수정은 매매 설정이, 삭제는 전략 자체가 노출·파괴된다.

빠져도 자기 스윙은 정상 동작하므로 사용 중에 드러나지 않는다 — 그래서 테스트로 고정한다.
"""
import unittest
from types import SimpleNamespace

from app.domain.swing import service as swing_svc
from app.exceptions import NotFoundError


OWNER = "owner"
INTRUDER = "intruder"


class FakeRepo:
    """ACCOUNT 조인 대역 — 소유자에게만 스윙을 돌려준다"""

    def __init__(self):
        self.swing = SimpleNamespace(
            SWING_ID=1, ACCOUNT_NO="acc-a", ST_CODE="AMD", MRKT_CODE="NAS",
            USE_YN="Y", SIGNAL=0, HOLD_QTY=0,
        )
        self.deleted = []
        self.ema_deleted = []

    async def find_by_id_with_ownership(self, user_id, swing_id):
        return self.swing if user_id == OWNER else None

    async def find_by_id(self, swing_id):
        """소유권을 보지 않는 옛 경로. 검증이 여기로 되돌아가면 테스트가 잡아낸다."""
        return self.swing

    async def delete(self, swing_id):
        self.deleted.append(swing_id)
        return True

    async def exists_by_account_stock(self, account_no, st_code):
        return False

    async def delete_ema_option(self, account_no, st_code):
        self.ema_deleted.append((account_no, st_code))
        return True


class FakeDB:
    async def commit(self):
        pass

    async def rollback(self):
        pass


def _service():
    service = swing_svc.SwingService(FakeDB())
    service.repo = FakeRepo()
    return service


class SwingOwnershipTest(unittest.IsolatedAsyncioTestCase):

    async def test_owner_can_read(self):
        """소유자 조회는 그대로 동작한다 (과잉 차단 방지)"""
        result = await _service().get_swing(OWNER, 1)
        self.assertEqual(result["SWING_ID"], 1)

    async def test_intruder_cannot_read(self):
        """남의 스윙은 '없음'으로 응답한다

        403으로 구분하면 해당 ID의 스윙이 존재한다는 사실이 새어 나간다.
        """
        with self.assertRaises(NotFoundError):
            await _service().get_swing(INTRUDER, 1)

    async def test_intruder_cannot_delete(self):
        """남의 스윙은 삭제되지 않는다 — 전략과 이평선 설정이 통째로 사라진다"""
        service = _service()

        with self.assertRaises(NotFoundError):
            await service.delete_swing(INTRUDER, 1, "S")

        self.assertEqual(service.repo.deleted, [], "남의 스윙이 삭제됐다")
        self.assertEqual(service.repo.ema_deleted, [])

    async def test_owner_can_delete(self):
        service = _service()

        await service.delete_swing(OWNER, 1, "S")

        self.assertEqual(service.repo.deleted, [1])

    async def test_intruder_cannot_update(self):
        """남의 스윙 설정은 수정되지 않는다"""
        with self.assertRaises(NotFoundError):
            await _service().update_swing(1, {"USE_YN": "N"}, INTRUDER)


if __name__ == "__main__":
    unittest.main()
