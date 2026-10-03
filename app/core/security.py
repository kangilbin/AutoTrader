"""
보안 관련 유틸리티
- JWT 토큰 생성/검증
- AES 암호화
"""
import base64
import os
import jwt
from datetime import datetime
from typing import Optional
from jwt.exceptions import ExpiredSignatureError
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from pydantic import BaseModel
from app.exceptions import AuthenticationError
from app.core.config import get_settings


# ==================== Models ====================

class Token(BaseModel):
    """토큰 응답 모델"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """토큰 페이로드 데이터"""
    user_id: str | None = None


# ==================== JWT ====================

def create_access_token(user_id: str, user_info: dict = None) -> str:
    """액세스 토큰 생성"""
    settings = get_settings()
    to_encode = {
        "sub": user_id,
        "exp": datetime.now() + settings.token_access_exp,
        "user_claims": user_info or {}
    }
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    """리프레시 토큰 생성"""
    settings = get_settings()
    to_encode = {
        "sub": user_id,
        "exp": datetime.now() + settings.token_refresh_exp
    }
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str) -> Optional[TokenData]:
    """토큰 검증 및 페이로드 추출"""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
        return TokenData(user_id=user_id)
    except ExpiredSignatureError:
        raise AuthenticationError("토큰이 만료되었습니다.", reason="token_expired")
    except jwt.PyJWTError:
        raise AuthenticationError("유효하지 않은 토큰입니다.", reason="invalid_token")


# ==================== AES Encryption ====================

def _get_aes_key() -> bytes:
    """AES 키 로드"""
    settings = get_settings()
    if not settings.AES_SECRET_KEY:
        raise ValueError("AES_SECRET_KEY가 설정되지 않았습니다")
    key = base64.b64decode(settings.AES_SECRET_KEY)
    if len(key) != 16:
        raise ValueError("AES_SECRET_KEY는 16바이트여야 합니다 (Base64 인코딩)")
    return key


def encrypt(plain_text: str) -> str:
    """AES-128 GCM 암호화"""
    key = _get_aes_key()
    iv = os.urandom(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted_bytes = encryptor.update(plain_text.encode()) + encryptor.finalize()
    return base64.b64encode(iv + encryptor.tag + encrypted_bytes).decode()


def decrypt(encrypted_text: str) -> str:
    """AES-128 GCM 복호화"""
    key = _get_aes_key()
    raw_data = base64.b64decode(encrypted_text)
    iv, tag, encrypted_bytes = raw_data[:12], raw_data[12:28], raw_data[28:]
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv, tag), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted_bytes = decryptor.update(encrypted_bytes) + decryptor.finalize()
    return decrypted_bytes.decode()