import base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from app.module.config import get_env
import os

########### AES 암복호화 ###########


# 환경변수에서 시크릿 키 불러오기
SECRET_KEY = get_env("AES_SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("SECRET_KEY가 설정되지 않았습니다! .env 파일을 확인하세요.")

# Base64 디코딩하여 16바이트 키로 변환
SECRET_KEY = base64.b64decode(SECRET_KEY)
if len(SECRET_KEY) != 16:
    raise ValueError("SECRET_KEY는 AES-128을 위해 16바이트여야 합니다!")


def encrypt(plain_text: str) -> str:
    """AES-128 GCM 방식으로 암호화"""
    iv = os.urandom(12)  # IV (Initialization Vector) 12바이트
    cipher = Cipher(algorithms.AES(SECRET_KEY), modes.GCM(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted_bytes = encryptor.update(plain_text.encode()) + encryptor.finalize()

    return base64.b64encode(iv + encryptor.tag + encrypted_bytes).decode()  # IV + 태그 + 암호문


def decrypt(encrypted_text: str) -> str:
    """AES-128 GCM 방식으로 복호화"""
    raw_data = base64.b64decode(encrypted_text)
    iv, tag, encrypted_bytes = raw_data[:12], raw_data[12:28], raw_data[28:]

    cipher = Cipher(algorithms.AES(SECRET_KEY), modes.GCM(iv, tag), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted_bytes = decryptor.update(encrypted_bytes) + decryptor.finalize()

    return decrypted_bytes.decode()
