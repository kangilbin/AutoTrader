import bcrypt


# 비밀번호를 해시화하는 함수
def hash_password(password: str) -> str:
    """
    주어진 비밀번호를 해시화하고 솔트를 추가하여 반환합니다.
    :param password: 해시화할 평문 비밀번호
    :return: 해시화된 비밀번호 (솔트 포함)
    """
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
    return hashed.decode()


# 해시된 비밀번호와 사용자가 입력한 비밀번호를 비교하는 함수
def check_password(plain_password: str, hashed_password: str) -> bool:
    """
    입력한 비밀번호와 저장된 해시된 비밀번호를 비교하여 일치 여부를 반환합니다.
    :param plain_password: 사용자가 입력한 평문 비밀번호
    :param hashed_password: 저장된 해시된 비밀번호
    :return: 비밀번호가 일치하면 True, 그렇지 않으면 False
    """
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())
