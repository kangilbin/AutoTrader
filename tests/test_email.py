"""
이메일 서비스 유닛 테스트
"""
import pytest
from unittest.mock import MagicMock, patch, call
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib

from app.common.email import EmailService


@pytest.mark.unit
@pytest.mark.email
class TestEmailService:
    """EmailService 테스트 클래스"""

    def test_send_device_registration_notification_success(
        self, mock_settings, sample_user_data
    ):
        """이메일 발송 성공 테스트"""
        with patch('app.common.email.get_settings', return_value=mock_settings), \
             patch('app.common.email.smtplib.SMTP') as mock_smtp:

            # Mock SMTP 서버 설정
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_server

            # 이메일 발송
            result = EmailService.send_device_registration_notification(
                user_id=sample_user_data['user_id'],
                user_name=sample_user_data['user_name'],
                device_id=sample_user_data['device_id'],
                device_name=sample_user_data['device_name']
            )

            # 검증
            assert result is True
            mock_smtp.assert_called_once_with(mock_settings.SMTP_HOST, mock_settings.SMTP_PORT)
            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once_with(
                mock_settings.SMTP_USER,
                mock_settings.SMTP_PASSWORD
            )
            mock_server.send_message.assert_called_once()

    def test_send_device_registration_notification_no_smtp_config(
        self, mock_settings_no_smtp, sample_user_data
    ):
        """SMTP 설정이 없을 때 테스트 (로그만 남기고 False 반환)"""
        with patch('app.common.email.get_settings', return_value=mock_settings_no_smtp), \
             patch('app.common.email.logger') as mock_logger:

            # 이메일 발송 시도
            result = EmailService.send_device_registration_notification(
                user_id=sample_user_data['user_id'],
                user_name=sample_user_data['user_name'],
                device_id=sample_user_data['device_id'],
                device_name=sample_user_data['device_name']
            )

            # 검증
            assert result is False
            mock_logger.warning.assert_called_once()
            mock_logger.info.assert_called_once()

            # 로그 메시지 검증
            warning_call = mock_logger.warning.call_args[0][0]
            assert "SMTP 설정이 없어" in warning_call

            info_call = mock_logger.info.call_args[0][0]
            assert sample_user_data['user_id'] in info_call
            assert sample_user_data['device_id'] in info_call

    def test_send_device_registration_notification_smtp_connection_error(
        self, mock_settings, sample_user_data
    ):
        """SMTP 연결 실패 테스트"""
        with patch('app.common.email.get_settings', return_value=mock_settings), \
             patch('app.common.email.smtplib.SMTP') as mock_smtp, \
             patch('app.common.email.logger') as mock_logger:

            # SMTP 연결 에러 발생
            mock_smtp.side_effect = smtplib.SMTPConnectError(421, "Connection refused")

            # 이메일 발송 시도
            result = EmailService.send_device_registration_notification(
                user_id=sample_user_data['user_id'],
                user_name=sample_user_data['user_name'],
                device_id=sample_user_data['device_id'],
                device_name=sample_user_data['device_name']
            )

            # 검증
            assert result is False
            mock_logger.error.assert_called_once()
            error_call = mock_logger.error.call_args[0][0]
            assert "이메일 발송 실패" in error_call

    def test_send_device_registration_notification_authentication_error(
        self, mock_settings, sample_user_data
    ):
        """SMTP 인증 실패 테스트"""
        with patch('app.common.email.get_settings', return_value=mock_settings), \
             patch('app.common.email.smtplib.SMTP') as mock_smtp, \
             patch('app.common.email.logger') as mock_logger:

            # Mock SMTP 서버 설정
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_server

            # 인증 에러 발생
            mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, "Authentication failed")

            # 이메일 발송 시도
            result = EmailService.send_device_registration_notification(
                user_id=sample_user_data['user_id'],
                user_name=sample_user_data['user_name'],
                device_id=sample_user_data['device_id'],
                device_name=sample_user_data['device_name']
            )

            # 검증
            assert result is False
            mock_logger.error.assert_called_once()

    def test_send_device_registration_notification_email_content(
        self, mock_settings, sample_user_data
    ):
        """이메일 내용 검증 테스트"""
        with patch('app.common.email.get_settings', return_value=mock_settings), \
             patch('app.common.email.smtplib.SMTP') as mock_smtp:

            # Mock SMTP 서버 설정
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_server

            # 이메일 발송
            EmailService.send_device_registration_notification(
                user_id=sample_user_data['user_id'],
                user_name=sample_user_data['user_name'],
                device_id=sample_user_data['device_id'],
                device_name=sample_user_data['device_name']
            )

            # send_message가 호출된 인자 가져오기
            assert mock_server.send_message.called
            sent_message = mock_server.send_message.call_args[0][0]

            # 이메일 헤더 검증
            assert sent_message['From'] == mock_settings.SMTP_USER
            assert sent_message['To'] == mock_settings.ADMIN_EMAIL
            assert sample_user_data['user_name'] in sent_message['Subject']
            assert 'AutoTrader' in sent_message['Subject']

            # 이메일 본문 검증 (multipart이므로 각 파트 확인)
            payload = sent_message.get_payload()
            assert len(payload) == 2  # text/plain + text/html

            # 텍스트 본문 검증 (디코딩 필요)
            text_part = payload[0].get_payload(decode=True).decode('utf-8')
            assert sample_user_data['user_id'] in text_part
            assert sample_user_data['user_name'] in text_part
            assert sample_user_data['device_id'] in text_part
            assert sample_user_data['device_name'] in text_part

            # HTML 본문 검증 (디코딩 필요)
            html_part = payload[1].get_payload(decode=True).decode('utf-8')
            assert sample_user_data['user_id'] in html_part
            assert sample_user_data['user_name'] in html_part
            assert sample_user_data['device_id'] in html_part
            assert sample_user_data['device_name'] in html_part
            assert '<html>' in html_part
            assert '</html>' in html_part

    def test_send_device_registration_notification_with_special_characters(
        self, mock_settings
    ):
        """특수 문자가 포함된 데이터 테스트"""
        with patch('app.common.email.get_settings', return_value=mock_settings), \
             patch('app.common.email.smtplib.SMTP') as mock_smtp:

            # Mock SMTP 서버 설정
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_server

            # 특수 문자 포함 데이터
            result = EmailService.send_device_registration_notification(
                user_id="test@user#123",
                user_name="홍길동 & 김철수",
                device_id="device-<uuid>-12345",
                device_name="iPhone 14 Pro (Max)"
            )

            # 검증
            assert result is True
            mock_server.send_message.assert_called_once()

    def test_send_device_registration_notification_smtp_starttls_error(
        self, mock_settings, sample_user_data
    ):
        """STARTTLS 실패 테스트"""
        with patch('app.common.email.get_settings', return_value=mock_settings), \
             patch('app.common.email.smtplib.SMTP') as mock_smtp, \
             patch('app.common.email.logger') as mock_logger:

            # Mock SMTP 서버 설정
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_server

            # STARTTLS 에러 발생
            mock_server.starttls.side_effect = smtplib.SMTPException("STARTTLS failed")

            # 이메일 발송 시도
            result = EmailService.send_device_registration_notification(
                user_id=sample_user_data['user_id'],
                user_name=sample_user_data['user_name'],
                device_id=sample_user_data['device_id'],
                device_name=sample_user_data['device_name']
            )

            # 검증
            assert result is False
            mock_logger.error.assert_called_once()

    def test_send_device_registration_notification_encoding(
        self, mock_settings
    ):
        """한글 인코딩 테스트"""
        with patch('app.common.email.get_settings', return_value=mock_settings), \
             patch('app.common.email.smtplib.SMTP') as mock_smtp:

            # Mock SMTP 서버 설정
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_server

            # 한글 데이터
            result = EmailService.send_device_registration_notification(
                user_id="한글아이디",
                user_name="테스트사용자",
                device_id="디바이스아이디",
                device_name="갤럭시 S24 Ultra"
            )

            # 검증
            assert result is True

            # 이메일이 정상적으로 전송되었는지 확인
            assert mock_server.send_message.called
            sent_message = mock_server.send_message.call_args[0][0]

            # UTF-8 인코딩 확인
            payload = sent_message.get_payload()
            text_part = payload[0]
            html_part = payload[1]

            assert text_part.get_content_charset() == 'utf-8'
            assert html_part.get_content_charset() == 'utf-8'