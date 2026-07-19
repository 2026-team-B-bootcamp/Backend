"""유저 프로필 수정/아바타 업로드 관련 비즈니스 로직.
요청 흐름: 클라이언트 -> routers/users.py -> 이 서비스 -> models/user.py(DB).
라우터에서 직접 DB를 다루지 않고, 이 서비스 계층을 거쳐 실제 처리를 위임한다.
"""

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

AVATAR_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "avatars"
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
MAX_AVATAR_BYTES = 5 * 1024 * 1024


# 프로필 수정 시 이메일이 이미 다른 유저에게 사용 중일 때 발생시키는 예외.
class EmailTakenError(Exception):
    pass


async def update_profile(db: AsyncSession, user: User, display_name: str, email: str) -> User:
    # 이메일을 바꾸려는 경우에만 다른 유저가 이미 쓰고 있는지 중복 체크한다.
    if email != user.email:
        existing = await db.scalar(select(User).where(User.email == email))
        if existing is not None:
            raise EmailTakenError
        user.email = email
    user.display_name = display_name
    await db.commit()
    await db.refresh(user)
    return user


# 새 아바타로 교체하기 전, 디스크에 남아있는 기존 아바타 파일을 지워서
# 파일이 계속 쌓이는 것을 막는다.
def _delete_existing_avatar(user: User) -> None:
    if not user.avatar_url:
        return
    filename = user.avatar_url.rsplit("/", 1)[-1]
    path = AVATAR_DIR / filename
    if path.exists():
        path.unlink()


async def save_avatar(db: AsyncSession, user: User, content_type: str, data: bytes) -> User:
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    _delete_existing_avatar(user)
    # 파일명이 겹치지 않도록 유저 id + 랜덤 문자열(uuid)로 새 이름을 만든다.
    ext = ALLOWED_CONTENT_TYPES[content_type]
    filename = f"{user.id}-{uuid.uuid4().hex}.{ext}"
    (AVATAR_DIR / filename).write_bytes(data)
    user.avatar_url = f"/static/avatars/{filename}"
    await db.commit()
    await db.refresh(user)
    return user
