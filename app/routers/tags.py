"""서버별 관심사 태그(최대 3개) 등록/수정과 서버 관심사 통계를 담당하는 라우터.

요청 흐름: 클라이언트 -> 이 라우터 -> tag_service -> 모델(DB).
여기서 저장된 태그는 servers.py의 멤버 목록, messages.py의 메시지에서
"나와 겹치는 관심사"를 계산하는 데 쓰인다 (이 서비스의 차별점 기능).

통계 엔드포인트(GET /{id}/tags/stats)는 태그를 처음 설정하는 사람에게
"이 모임엔 어떤 관심사가 모여 있는지"를 보여준다. 집계는 DB에서 매번 계산하고,
비싼 AI 요약만 services/ai/tag_stats.py가 Redis에 캐시한다.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.server import ServerMember
from app.models.user import User
from app.schemas.tag import TagResponse, TagStatEntry, TagStatsResponse, TagUpsertRequest
from app.services import server_service, tag_service
from app.services.ai import tag_stats
from app.services.ai.base import TagInsightProvider
from app.services.ai.provider import get_tag_insight_provider

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


@router.get("/{server_id}/tags/stats", response_model=TagStatsResponse)
async def tag_stats_endpoint(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    provider: TagInsightProvider = Depends(get_tag_insight_provider),
) -> TagStatsResponse:
    """이 서버의 관심사 분포 + AI 한줄 요약 + 추천 태그.

    태그 설정 모달이 열릴 때 한 번 호출한다. 요약·추천은 태그 분포가 그대로면
    Redis 캐시에서 나오므로(tag_stats.get_insight) 반복 호출이 LLM 비용으로 이어지지 않는다.
    """
    await server_service.require_membership(db, server_id, current_user.id)

    tags_map = await tag_service.get_server_tags_map(db, server_id)
    counts = tag_stats.aggregate(tags_map)
    tagged_members = sum(
        1 for tags in tags_map.values() if any(t and t.strip() for t in tags)
    )
    total_members = await db.scalar(
        select(func.count()).select_from(ServerMember).where(ServerMember.server_id == server_id)
    )

    insight = await tag_stats.get_insight(provider, server_id, counts, tagged_members)

    return TagStatsResponse(
        total_members=total_members or 0,
        tagged_members=tagged_members,
        top_tags=[
            TagStatEntry(tag=tag, count=count) for tag, count in counts[: tag_stats.TOP_TAGS]
        ],
        summary=insight.summary,
        suggestions=insight.suggestions,
        my_tags=[t for t in tags_map.get(current_user.id, []) if t and t.strip()],
    )
