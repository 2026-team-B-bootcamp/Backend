from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.ai import IcebreakerResponse
from app.services import server_service, tag_service
from app.services.ai.base import IcebreakerProvider
from app.services.ai.stub_provider import get_icebreaker_provider

router = APIRouter(prefix="/servers", tags=["ai"])


@router.post(
    "/{server_id}/members/{user_id}/icebreaker", response_model=IcebreakerResponse
)
async def icebreaker(
    server_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    provider: IcebreakerProvider = Depends(get_icebreaker_provider),
) -> IcebreakerResponse:
    await server_service.require_membership(db, server_id, current_user.id)
    if not await server_service.is_member(db, server_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Target is not a server member"
        )

    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    tag = await tag_service.get_user_tags(db, server_id, user_id)
    question = provider.generate_icebreaker(target.display_name, tag_service.tag_values(tag))
    return IcebreakerResponse(question=question)
