"""
이메일 발송 서비스
"""
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class EmailService:
    """이메일 발송 서비스"""

    @staticmethod
    def send_device_registration_notification(
        user_id: str,
        user_name: str,
        device_id: str,
        device_name: str
    ) -> bool:
        """
        디바이스 등록 알림 이메일 발송

        Args:
            user_id: 사용자 ID
            user_name: 사용자 이름
            device_id: 디바이스 ID
            device_name: 디바이스 이름

        Returns:
            성공 여부
        """
        # 함수 내부에서 settings 가져오기 (테스트 모킹을 위해)
        settings = get_settings()

        # SMTP 설정이 없으면 로그만 남기고 스킵
        if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
            logger.warning("SMTP 설정이 없어 이메일을 보낼 수 없습니다. 로그만 기록합니다.")
            logger.info(
                f"[디바이스 등록 알림] "
                f"사용자: {user_name}({user_id}), "
                f"디바이스: {device_name}({device_id})"
            )
            return False

        try:
            # 이메일 메시지 생성
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"[AutoTrader] 새로운 디바이스 등록 요청 - {user_name}"
            msg['From'] = settings.SMTP_USER
            msg['To'] = settings.ADMIN_EMAIL

            # HTML 본문
            html_body = f"""
            <html>
              <head></head>
              <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px;">
                  <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px;">
                    🔔 새로운 디바이스 등록 요청
                  </h2>

                  <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <h3 style="color: #495057; margin-top: 0;">회원 정보</h3>
                    <p><strong>사용자 ID:</strong> {user_id}</p>
                    <p><strong>사용자 이름:</strong> {user_name}</p>
                  </div>

                  <div style="background-color: #fff3cd; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <h3 style="color: #856404; margin-top: 0;">디바이스 정보</h3>
                    <p><strong>디바이스 ID:</strong> <code style="background-color: #e9ecef; padding: 2px 6px; border-radius: 3px;">{device_id}</code></p>
                    <p><strong>디바이스 이름:</strong> {device_name}</p>
                  </div>

                  <div style="margin: 20px 0; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #2196F3; border-radius: 3px;">
                    <p style="margin: 0;"><strong>📌 알림:</strong> 회원가입이 완료되었습니다. 디바이스를 활성화해 주세요.</p>
                  </div>

                  <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">

                  <p style="color: #6c757d; font-size: 12px;">
                    <strong>발송 시간:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>
                    <strong>발송자:</strong> AutoTrader System
                  </p>
                </div>
              </body>
            </html>
            """

            # 텍스트 본문 (HTML을 지원하지 않는 이메일 클라이언트용)
            text_body = f"""
            [AutoTrader] 새로운 디바이스 등록 요청
            
            회원 정보:
            - 사용자 ID: {user_id}
            - 사용자 이름: {user_name}
            
            디바이스 정보:
            - 디바이스 ID: {device_id}
            - 디바이스 이름: {device_name}
            
            알림: 회원가입이 완료되었습니다. 디바이스를 활성화 해주세요.
            
            발송 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            발송자: AutoTrader System
            """

            # 본문 추가
            part1 = MIMEText(text_body, 'plain', 'utf-8')
            part2 = MIMEText(html_body, 'html', 'utf-8')
            msg.attach(part1)
            msg.attach(part2)

            # SMTP 서버 연결 및 전송
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.starttls()  # TLS 시작
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)

            logger.info(
                f"디바이스 등록 알림 이메일 발송 성공: "
                f"{settings.ADMIN_EMAIL} (사용자: {user_id})"
            )
            return True

        except Exception as e:
            logger.error(f"이메일 발송 실패: {e}", exc_info=True)
            return False