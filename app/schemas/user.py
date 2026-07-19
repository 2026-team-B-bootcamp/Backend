"""유저 정보 응답/수정 요청의 형태를 정의하는 pydantic 스키마.
UserResponse는 클라이언트에게 유저 정보를 내려줄 때(password_hash는 절대 포함하지 않음),
UpdateUserRequest는 프로필 수정 요청 바디를 검증할 때 사용한다.
"""

from pydantic import BaseModel, Field


class UserResponse(BaseModel):
    id: int
    email: str
    display_name: str
    avatar_url: str | None = None


class UpdateUserRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=255)
