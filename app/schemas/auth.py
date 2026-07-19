"""회원가입/로그인 요청 및 응답 바디의 형태를 정의하는 pydantic 스키마.
요청 흐름: 클라이언트가 보낸 JSON -> 여기서 형식/길이 검증 -> routers/auth.py로 전달.
검증에 실패하면(예: 비밀번호가 8자 미만) FastAPI가 자동으로 422 에러를 응답한다.
"""

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=100)


class LoginRequest(BaseModel):
    email: str
    password: str


# 로그인/회원가입 성공 시 클라이언트에게 내려주는 응답 형태.
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
