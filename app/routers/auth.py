"""회원가입/로그인 HTTP 엔드포인트 정의.
요청 흐름: 클라이언트 -> 이 라우터(/auth/signup, /auth/login)
-> core/security.py(비밀번호 해싱·JWT 발급) -> models/user.py(DB 조회/저장).
성공 시 두 엔드포인트 모두 JWT access token을 응답으로 돌려준다.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import LoginRequest, SignupRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse)
async def signup(payload: SignupRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    # 이메일 중복 가입 방지.
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )
    # 비밀번호는 원문이 아닌 해시(hash_password)로만 저장한다.
    user = User(
        email=payload.email,
        password_hash=await hash_password(payload.password),
        display_name=payload.display_name,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    # 가입 직후 바로 로그인 상태로 만들어주기 위해 토큰을 발급해서 응답한다.
    return TokenResponse(access_token=create_access_token(str(user.id), user.token_version))


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    # 이메일이 없거나 비밀번호가 틀리면 이유를 구분하지 않고 같은 에러로 응답한다
    # (계정 존재 여부가 노출되지 않도록 하기 위함).
    user = await db.scalar(select(User).where(User.email == payload.email))
    # 게스트(슬랙 경유 자동 생성)는 비밀번호 자체가 없다. password_hash가 NULL이라
    # verify_password에 넘기면 argon2가 InvalidHash를 던져 500이 난다 —
    # 그 전에 다른 실패와 똑같은 401로 끊는다.
    if user is None or user.is_guest or user.password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    if not await verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    return TokenResponse(access_token=create_access_token(str(user.id), user.token_version))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    # 토큰 버전을 올려 지금까지 발급된 이 유저의 모든 토큰을 무효화한다(전 기기 로그아웃).
    # 이후 옛 토큰으로 오는 REST·WS 요청은 get_current_user/ws에서 401로 거부된다.
    current_user.token_version += 1
    await db.commit()
