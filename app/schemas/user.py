"""유저 정보 응답/수정 요청의 형태를 정의하는 pydantic 스키마.
UserResponse는 클라이언트에게 유저 정보를 내려줄 때(password_hash는 절대 포함하지 않음),
UpdateUserRequest는 프로필 수정 요청 바디를 검증할 때 사용한다.
"""

from pydantic import BaseModel, Field


class UserResponse(BaseModel):
    id: int
    # 슬랙 경유 게스트는 이메일이 없다(models/user.py의 is_guest). 프론트는 이미
    # 이 필드를 프로필 화면에서만 쓰므로 null이면 빈 값으로 보인다.
    email: str | None = None
    display_name: str
    avatar_url: str | None = None
    is_guest: bool = False


class UpdateUserRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=255)
