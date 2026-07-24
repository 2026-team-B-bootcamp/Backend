"""내 프로필(이름/이메일) 조회·수정, 아바타 이미지 업로드를 담당하는 라우터.

요청 흐름: 클라이언트 -> 이 라우터 -> user_service -> 모델(DB).
현재 로그인한 사용자(get_current_user) 본인 정보만 다룬다.
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.user import UpdateUserRequest, UserResponse
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


def _to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        is_guest=user.is_guest,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return _to_response(current_user)


@router.patch("/me", response_model=UserResponse)
async def update_me(
    payload: UpdateUserRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    try:
        user = await user_service.update_profile(
            db, current_user, payload.display_name.strip(), payload.email.strip()
        )
    except user_service.EmailTakenError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 사용 중인 이메일입니다"
        ) from None
    return _to_response(user)


@router.post("/me/avatar", response_model=UserResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    # 허용된 이미지 형식(jpg/png/webp)인지 먼저 검사.
    if file.content_type not in user_service.ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="jpg, png, webp 이미지만 업로드할 수 있습니다",
        )
    data = await file.read()
    if len(data) > user_service.MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이미지 용량은 5MB 이하여야 합니다",
        )
    user = await user_service.save_avatar(db, current_user, file.content_type, data)
    return _to_response(user)
