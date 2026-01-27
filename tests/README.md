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
├── conftest.py          # pytest fixtures
├── test_email.py        # 이메일 서비스 테스트
└── README.md            # 이 파일
```

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