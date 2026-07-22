"""AI 아이스브레이커(대화 시작 질문) API 라우터.

요청 흐름: 클라이언트 → 이 라우터 → tag_service(대상의 관심사 태그 조회)
→ ai.service(캐시 조회 → provider로 질문 배치 생성). provider는 GEMINI_API_KEY
유무에 따라 Gemini 또는 stub 템플릿이 주입된다(services/ai/provider.py).
유저별 rate limit(기본 10회/시간)으로 LLM 비용 남용을 막는다.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.ai import IcebreakerRequest, IcebreakerResponse
from app.services import server_service, tag_service
from app.services.ai import service as ai_service
from app.services.ai.base import IcebreakerProvider
from app.services.ai.provider import get_icebreaker_provider
from app.services.ai.rate_limit import RateLimiter, get_ai_rate_limiter

router = APIRouter(prefix="/servers", tags=["ai"])


@router.post(
    "/{server_id}/members/{user_id}/icebreaker", response_model=IcebreakerResponse
)
async def icebreaker(
    server_id: int,
    user_id: int,
    payload: IcebreakerRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    provider: IcebreakerProvider = Depends(get_icebreaker_provider),
    limiter: RateLimiter = Depends(get_ai_rate_limiter),
) -> IcebreakerResponse:
    # LLM 비용 남용 방지 — 한도 초과 시 여기서 429로 끊는다 (권한 검사보다 싸므로 먼저).
    await limiter.check(current_user.id)
    # 요청자가 서버 멤버인지, 질문 대상(target)도 같은 서버 멤버인지 먼저 확인한다.
    await server_service.require_membership(db, server_id, current_user.id)
    if not await server_service.is_member(db, server_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Target is not a server member"
        )

    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    tag_row = await tag_service.get_user_tags(db, server_id, user_id)
    all_tags = [t for t in tag_service.tag_values(tag_row) if t and t.strip()]

    # 어떤 관심사로 질문할지는 유저가 모달에서 고른다. 선택된 태그가 대상의 실제
    # 태그의 부분집합인지 검증한다 — 임의 문자열이 그대로 LLM 프롬프트와 전역 캐시
    # 키에 들어가는 것(프롬프트 인젝션·캐시 오염)을 막기 위해서다.
    if payload is not None and payload.tags is not None:
        selected = [t.strip() for t in payload.tags if t and t.strip()]
        if not selected:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="관심사를 최소 1개 선택해야 합니다",
            )
        if not set(selected) <= set(all_tags):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="대상 멤버의 관심사에 없는 태그입니다",
            )
    else:
        # body 없이 호출하면 태그 전체 사용 (태그가 없는 멤버도 일반 질문으로 동작).
        selected = all_tags

    # 캐시 계층을 거치므로 같은 태그 조합이면 LLM 재호출 없이 캐시된
    # 템플릿들에 이름만 끼워 반환한다.
    questions = await ai_service.get_icebreakers(provider, target.display_name, selected)
    return IcebreakerResponse(questions=questions)
