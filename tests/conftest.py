"""
pytest 설정 및 공통 fixtures
"""
import pytest
from unittest.mock import MagicMock, patch
from app.core.config import Settings


@pytest.fixture
def mock_settings():
    """Mock Settings fixture"""
    settings = MagicMock(spec=Settings)
    settings.SMTP_HOST = "smtp.gmail.com"
    settings.SMTP_PORT = 587
    settings.SMTP_USER = "test@example.com"
    settings.SMTP_PASSWORD = "test-password"
    settings.ADMIN_EMAIL = "kib3388@naver.com"
    return settings


@pytest.fixture
def mock_settings_no_smtp():
    """SMTP 설정이 없는 Mock Settings fixture"""
    settings = MagicMock(spec=Settings)
    settings.SMTP_HOST = "smtp.gmail.com"
    settings.SMTP_PORT = 587
    settings.SMTP_USER = None
    settings.SMTP_PASSWORD = None
    settings.ADMIN_EMAIL = "kib3388@naver.com"
    return settings


@pytest.fixture
def sample_user_data():
    """샘플 사용자 데이터"""
    return {
        "user_id": "testuser123",
        "user_name": "홍길동",
        "device_id": "device-uuid-12345",
        "device_name": "iPhone 14 Pro"
    }