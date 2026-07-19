from pydantic import BaseModel


class IcebreakerResponse(BaseModel):
    question: str
