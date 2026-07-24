"""슬랙 유저에게만 발급되는 개인 입장 링크.

채널에 공용 링크 하나를 뿌리면 누가 눌렀는지 알 수 없어 "계정당 1슬롯"을 지킬 수
없다(슬랙봇-신원고정.md §1). 슬랙이 유저 신원을 확정해주는 순간은 커맨드 실행과
버튼 클릭 둘뿐이므로, **그 순간에 신원을 박아 넣은 링크**를 본인에게만 준다.

토큰은 기존 로그인 토큰과 같은 형식이다 — 웹의 인증 경로(core/deps.py)를 그대로
타므로 별도 검증 코드가 필요 없다. 다만 유출 대비로 수명을 15분으로 줄인다.
"""

from urllib.parse import urlencode

from app.core.config import settings
from app.core.security import create_access_token
from app.models.channel import Channel
from app.models.user import User
from app.slack.features import Feature

# 링크를 받고 실제로 들어가기까지의 여유. 채널에 남아도 15분 뒤엔 무용지물이 된다.
LINK_TOKEN_EXPIRE_MINUTES = 15


def build_entry_link(user: User, channel: Channel, feature: Feature) -> str:
    """이 유저로 로그인된 채로 해당 기능이 열린 채널 화면으로 가는 링크."""
    token = create_access_token(
        str(user.id), user.token_version, expire_minutes=LINK_TOKEN_EXPIRE_MINUTES
    )
    query = urlencode({"t": token, "open": feature.open})
    base = settings.public_web_url.rstrip("/")
    return f"{base}/servers/{channel.server_id}/channels/{channel.id}?{query}"
