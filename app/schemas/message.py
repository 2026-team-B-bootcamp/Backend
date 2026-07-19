from datetime import datetime

from pydantic import BaseModel, Field


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=1000)


class MessageOut(BaseModel):
    id: int
    user_id: int
    display_name: str
    tags: list[str] = []
    content: str
    created_at: datetime
