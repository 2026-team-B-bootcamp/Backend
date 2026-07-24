"""비밀번호 해싱과 JWT 토큰 생성/검증을 담당하는 보안 유틸리티.
회원가입/로그인(routers/auth.py)과 로그인 유저 확인(core/deps.py)에서 사용된다.
비밀번호는 원문 그대로 저장하지 않고 항상 이 파일을 거쳐 해시로 변환한다.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

# argon2는 현재 권장되는 비밀번호 해싱 알고리즘이다(단방향, 복호화 불가).
_hasher = PasswordHasher()


async def hash_password(password: str) -> str:
    # 회원가입 시 원문 비밀번호를 DB에 저장 가능한 해시 문자열로 바꾼다.
    # argon2는 의도적으로 느린 CPU 작업이라 이벤트 루프에서 직접 돌리면 그동안
    # 다른 요청(채팅·게임 등)이 전부 멈춘다. 스레드로 넘겨 루프를 막지 않는다.
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password: str, password_hash: str) -> bool:
    # 로그인 시 입력한 비밀번호와 저장된 해시를 비교한다.
    # 해시는 되돌릴 수 없으므로 "같은 방식으로 다시 해싱해서 비교"하는 방식으로 검증한다.
    # hash와 같은 이유로 스레드에서 실행한다(동시 로그인 시 루프 스톨 방지).
    def _verify() -> bool:
        try:
            return _hasher.verify(password_hash, password)
        except VerifyMismatchError:
            return False

    return await asyncio.to_thread(_verify)


def create_access_token(
    subject: str, token_version: int = 0, expire_minutes: int | None = None
) -> str:
    # subject(보통 user id)를 담은 JWT를 발급한다. exp(만료시간)가 지나면 토큰은 무효가 된다.
    # ver: 발급 시점의 유저 token_version. 검증 때 DB 값과 대조해 서버측 무효화를 구현한다.
    # expire_minutes: 기본(24시간)보다 짧게 쓰고 싶을 때만 지정한다. 슬랙 봇이 채널에
    #   뿌리는 입장 링크가 그 경우로, 링크가 유출돼도 피해가 짧게 끝나도록 15분을 준다.
    minutes = settings.jwt_expire_minutes if expire_minutes is None else expire_minutes
    expire = datetime.now(UTC) + timedelta(minutes=minutes)
    payload = {"sub": subject, "ver": token_version, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.algorithm)


def decode_access_token(token: str) -> dict:
    # 서명과 만료시간을 검증하고 토큰 안의 payload(sub, exp 등)를 꺼낸다.
    # 서명이 다르거나 만료됐으면 예외(jwt.PyJWTError)가 발생한다.
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.algorithm])
