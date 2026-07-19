from pydantic import BaseModel, Field


class ServerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ServerJoinRequest(BaseModel):
    invite_code: str = Field(min_length=1, max_length=8)


class ServerResponse(BaseModel):
    id: int
    name: str
    invite_code: str


class MemberResponse(BaseModel):
    user_id: int
    display_name: str
    tags: list[str]
    common_with_me: list[str]
