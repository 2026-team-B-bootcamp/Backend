from pydantic import BaseModel, Field


class TagUpsertRequest(BaseModel):
    tag1: str = Field(min_length=1, max_length=30)
    tag2: str = Field(min_length=1, max_length=30)
    tag3: str = Field(min_length=1, max_length=30)


class TagResponse(BaseModel):
    tags: list[str]
