# 테스트 가이드

## 설치

테스트 실행을 위해 필요한 패키지를 설치합니다:

```bash
# pytest 및 관련 플러그인 설치
pip install pytest pytest-cov pytest-asyncio
```

또는 uv를 사용하는 경우:

```bash
uv pip install pytest pytest-cov pytest-asyncio
```

## 테스트 실행

### 전체 테스트 실행

```bash
# 프로젝트 루트에서
pytest

# 또는 상세 출력
pytest -v
```

### 특정 테스트 파일 실행

```bash
# 이메일 테스트만 실행
pytest tests/test_email.py

# 상세 출력
pytest tests/test_email.py -v
```

### 특정 테스트 케이스 실행

```bash
# 클래스 단위
pytest tests/test_email.py::TestEmailService

# 메서드 단위
pytest tests/test_email.py::TestEmailService::test_send_device_registration_notification_success
```

### 마커로 필터링

```bash
# 이메일 관련 테스트만
pytest -m email

# 유닛 테스트만
pytest -m unit

# 느린 테스트 제외
pytest -m "not slow"
```

### 커버리지 확인

```bash
# 커버리지 리포트와 함께 실행
pytest --cov=app --cov-report=html

# 커버리지 결과는 htmlcov/index.html에서 확인
open htmlcov/index.html
```

## 테스트 구조

```
tests/
├── __init__.py
├── conftest.py                    # pytest fixtures
├── test_email.py                  # 이메일 서비스 테스트 (pytest 필요)
├── test_swing_entity.py           # SwingTrade SIGNAL 상태머신 (unittest)
├── test_price_adjustment_batch.py # 수정주가 재적재 (unittest)
├── test_swing_trade_scenarios.py  # 스윙 매매 시나리오 통합 (unittest)
└── README.md                      # 이 파일
```

### test_swing_trade_scenarios.py

매수/매도 전 흐름을 사이클 단위로 실행해 검증합니다. 실제 엔티티(`SwingTrade`)와
실제 전략(`SingleEMAStrategy`)을 그대로 쓰고 DB/Redis/KIS API만 대체하므로,
매매 로직을 수정한 뒤 이 파일을 돌리면 회귀를 바로 잡을 수 있습니다.

```bash
PYTHONPATH=. python -m unittest tests.test_swing_trade_scenarios -v
```

검증 항목:

| 시나리오 | 확인 내용 |
|---|---|
| 매수 단일 체결 | SIGNAL 0→1, 보유수량/평단가/PEAK, 가용금액 차감, 이력 1건 |
| 매수 주문 단가 | 해외는 매도1호가(ask) 지정가로 전송 |
| 손절 전량 매도 | SIGNAL→3, 평단가 초기화, 매도 대금이 **체결가** 기준으로 가산 |
| 1차/2차 익절 | SIGNAL 1→2→3, 절반 매도, 1차 후 평단가 유지(본전방어) |
| 쿨다운 | SIGNAL 3→4→0 |
| 분할 매수(TWAP) | 첫 chunk 이력 저장, 보유수량=주문합계, 가용금액 정합 |
| 부분 체결 | 잔량 취소 1회, 체결분만 반영 |
| 분할 중 급락 | 1사이클 매수 중단 → 다음 사이클 손절 (의도된 동작) |
| 장 시간 가드 | 정규장 밖에는 주문 없음 |

> **중요**: TRADE_HISTORY 저장은 `order_executor` 한 곳에서만 이뤄져야 합니다.
> 배치가 중복 저장하면 실현손익이 왜곡되므로 이력 **건수**까지 검증합니다.

## 테스트 케이스 설명

### test_email.py

1. **test_send_device_registration_notification_success**
   - 정상적인 이메일 발송 테스트
   - SMTP 서버 연결 및 인증 확인

2. **test_send_device_registration_notification_no_smtp_config**
   - SMTP 설정이 없을 때 동작 확인
   - 로그만 남기고 False 반환

3. **test_send_device_registration_notification_smtp_connection_error**
   - SMTP 연결 실패 시 에러 처리

4. **test_send_device_registration_notification_authentication_error**
   - SMTP 인증 실패 시 에러 처리

5. **test_send_device_registration_notification_email_content**
   - 이메일 제목, 본문, 헤더 내용 검증
   - HTML/텍스트 멀티파트 확인

6. **test_send_device_registration_notification_with_special_characters**
   - 특수 문자 처리 확인

7. **test_send_device_registration_notification_smtp_starttls_error**
   - STARTTLS 실패 처리

8. **test_send_device_registration_notification_encoding**
   - 한글 인코딩 테스트 (UTF-8)

## 모킹 (Mocking)

테스트에서는 실제 SMTP 서버에 연결하지 않고 `unittest.mock`을 사용하여 모킹합니다:

- `smtplib.SMTP`: SMTP 서버 연결 모킹
- `get_settings()`: 설정 값 모킹
- `logger`: 로그 출력 모킹

## 주의사항

- 실제 이메일을 발송하지 않으므로 안전하게 테스트 가능
- SMTP 설정이 필요 없음
- 빠른 실행 속도

## 추가 테스트 작성

새로운 테스트를 추가하려면:

1. `tests/` 디렉토리에 `test_*.py` 파일 생성
2. `conftest.py`에 필요한 fixtures 추가
3. pytest 마커를 사용하여 테스트 분류

예시:
```python
import pytest

@pytest.mark.unit
@pytest.mark.email
def test_my_new_feature():
    # 테스트 코드
    pass
```