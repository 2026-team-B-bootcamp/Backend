"""링크 미리보기(OpenGraph 언퍼) 응답 스키마 (routers/link_preview.py에서 사용)."""

from pydantic import BaseModel


class LinkPreviewResponse(BaseModel):
    # 미리보기를 만든 원본 URL(리다이렉트 전 요청한 그 주소).
    url: str
    # OG 태그가 없을 수 있으니 제목/설명/이미지/사이트명 모두 비어 있을 수 있다.
    title: str | None = None
    description: str | None = None
    image: str | None = None
    site_name: str | None = None
