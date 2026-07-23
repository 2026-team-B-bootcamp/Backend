"""FastAPI 의존성(Dependency) 함수 모음.
라우터가 `Depends(...)`로 이 함수들을 호출해 DB 세션을 얻거나 로그인한 유저를 확인한다.
특히 get_current_user는 "요청 헤더의 JWT 토큰 -> 로그인한 유저" 로 변환하는
이 프로젝트 인증 흐름의 핵심 지점이다.
"""

from collections.abc import AsyncGenerator

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.base import async_session_maker
from app.models.user import User


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    # 요청 하나마다 새 DB 세션을 열고, 응답이 끝나면 자동으로 정리(close)한다.
    async with async_session_maker() as session:
        yield session


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    # 로그인 관련 실패는 모두 같은 401 에러로 응답한다(어떤 이유인지 노출하지 않음).
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    # "Authorization: Bearer <토큰>" 형식이 아니면 바로 거부.
    if not authorization or not authorization.lower().startswith("bearer "):
        raise credentials_error
    token = authorization.split(" ", 1)[1]
    try:
        # 토큰을 검증/해독해서 로그인 시 저장했던 유저 id(sub)를 꺼낸다.
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise credentials_error from None

    # 토큰이 유효해도 해당 유저가 DB에 실제로 있는지 다시 확인한다.
    user = await db.get(User, user_id)
    if user is None:
        raise credentials_error
    # 서버측 무효화: 로그아웃 등으로 token_version이 올라갔다면, 그 이전에 발급된
    # 토큰(ver 불일치)은 아직 만료 전이라도 거부한다. (ver 없는 옛 토큰은 0으로 취급)
    if int(payload.get("ver", 0)) != user.token_version:
        raise credentials_error
    return user
