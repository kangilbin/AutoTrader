"""상대 모멘텀 순위 유니버스 — 고정 목록(universe.json)

연구(scripts/research/portfolio_sim.py load_universe)에서 쓴 종목과 같은 목록이다. 실전이 연구와 같은 종목군을 보게 하려는 것이며,
목록을 바꾸면 백테스트 근거가 달라진다는 뜻이므로 바꿀 때는 연구를 다시 돌린다.

⚠️ 생존 편향: 현재 DB 에 남은 종목이라 연구 절대 수치가 낙관적이다 (universe.json 의 meta 참고).
이 목록의 종목은 매일 일봉이 수집돼야 순위가 최신이 된다 — STOCK_INFO.DATA_YN='Y' (scripts/momentum_seed_universe.py).
"""
import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple

UNIVERSE_FILE = Path(__file__).resolve().parent / "universe.json"


@lru_cache(maxsize=1)
def _load() -> Dict:
    return json.loads(UNIVERSE_FILE.read_text(encoding="utf-8"))


def universe_pairs(market: str) -> List[Tuple[str, str]]:
    """시장("KR"|"US")의 순위 유니버스 — [(시장코드, 종목코드)]"""
    if market not in ("KR", "US"):
        raise ValueError(f"알 수 없는 시장: {market}")
    return [(x["mrkt"], x["code"]) for x in _load()[market]]


def universe_names(market: str) -> Dict[str, str]:
    """종목코드 → 종목명 (로그·화면용)"""
    return {x["code"]: x.get("name", "") for x in _load()[market]}
