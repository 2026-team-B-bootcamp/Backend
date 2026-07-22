"""서버별 관심사 태그(최대 3개) 등록/수정을 담당하는 라우터.

요청 흐름: 클라이언트 -> 이 라우터 -> tag_service -> 모델(DB).
여기서 저장된 태그는 servers.py의 멤버 목록, messages.py의 메시지에서
"나와 겹치는 관심사"를 계산하는 데 쓰인다 (이 서비스의 차별점 기능).
"""

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
    # 새로 등장한 태그 텍스트만 임베딩해 저장한다 (유사 태그 매칭용).
    # 이미 임베딩된 태그는 API 호출 없이 넘어가고, 실패해도 태그 저장은 유지된다.
    await tag_service.ensure_embeddings(db, [payload.tag1, payload.tag2, payload.tag3])
    return TagResponse(tags=tag_service.tag_values(tag))
