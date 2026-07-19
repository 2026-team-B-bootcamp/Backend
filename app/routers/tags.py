from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.tag import TagResponse, TagUpsertRequest
from app.services import server_service, tag_service

router = APIRouter(prefix="/servers", tags=["tags"])


@router.put("/{server_id}/tags", response_model=TagResponse)
async def upsert_tags(
    server_id: int,
    payload: TagUpsertRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TagResponse:
    await server_service.require_membership(db, server_id, current_user.id)
    tag = await tag_service.upsert_tags(
        db, server_id, current_user.id, payload.tag1, payload.tag2, payload.tag3
    )
    return TagResponse(tags=tag_service.tag_values(tag))
