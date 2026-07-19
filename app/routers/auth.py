"""회원가입/로그인 HTTP 엔드포인트 정의.
요청 흐름: 클라이언트 -> 이 라우터(/auth/signup, /auth/login)
-> core/security.py(비밀번호 해싱·JWT 발급) -> models/user.py(DB 조회/저장).
성공 시 두 엔드포인트 모두 JWT access token을 응답으로 돌려준다.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
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
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    # 가입 직후 바로 로그인 상태로 만들어주기 위해 토큰을 발급해서 응답한다.
    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    # 이메일이 없거나 비밀번호가 틀리면 이유를 구분하지 않고 같은 에러로 응답한다
    # (계정 존재 여부가 노출되지 않도록 하기 위함).
    user = await db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    return TokenResponse(access_token=create_access_token(str(user.id)))
