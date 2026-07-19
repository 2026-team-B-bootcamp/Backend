"""비밀번호 해싱과 JWT 토큰 생성/검증을 담당하는 보안 유틸리티.
회원가입/로그인(routers/auth.py)과 로그인 유저 확인(core/deps.py)에서 사용된다.
비밀번호는 원문 그대로 저장하지 않고 항상 이 파일을 거쳐 해시로 변환한다.
"""

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

# argon2는 현재 권장되는 비밀번호 해싱 알고리즘이다(단방향, 복호화 불가).
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    # 회원가입 시 원문 비밀번호를 DB에 저장 가능한 해시 문자열로 바꾼다.
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    # 로그인 시 입력한 비밀번호와 저장된 해시를 비교한다.
    # 해시는 되돌릴 수 없으므로 "같은 방식으로 다시 해싱해서 비교"하는 방식으로 검증한다.
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_access_token(subject: str) -> str:
    # subject(보통 user id)를 담은 JWT를 발급한다. exp(만료시간)가 지나면 토큰은 무효가 된다.
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.algorithm)


def decode_access_token(token: str) -> dict:
    # 서명과 만료시간을 검증하고 토큰 안의 payload(sub, exp 등)를 꺼낸다.
    # 서명이 다르거나 만료됐으면 예외(jwt.PyJWTError)가 발생한다.
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.algorithm])
