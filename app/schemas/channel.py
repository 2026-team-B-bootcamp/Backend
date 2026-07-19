from pydantic import BaseModel, Field


class ChannelCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ChannelResponse(BaseModel):
    id: int
    server_id: int
    name: str
