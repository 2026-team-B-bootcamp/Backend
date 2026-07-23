"""링크 미리보기(OpenGraph 언퍼) API 라우터.

요청 흐름: 클라이언트가 채팅 메시지 속 링크를 발견 → 이 엔드포인트에 URL을 넘겨
서버가 대신 받아온 OG 메타(제목/설명/이미지)를 카드로 그린다.

미리보기를 만들 수 없으면(요청 실패·OG 태그 없음·SSRF 차단) 404를 준다.
프런트는 404를 조용히 무시(카드 미표시)하도록 되어 있다.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.link_preview import LinkPreviewResponse
from app.services.link_preview.service import fetch_preview

router = APIRouter(tags=["link-preview"])


@router.get("/link-preview", response_model=LinkPreviewResponse)
async def get_link_preview(
    url: str = Query(min_length=1, max_length=2048),
    current_user: User = Depends(get_current_user),
) -> LinkPreviewResponse:
    preview = await fetch_preview(url)
    if preview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="미리보기를 만들 수 없어요"
        )
    return LinkPreviewResponse(**preview)
