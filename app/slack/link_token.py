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
    """이 유저로 로그인된 채로, 그 기능만 있는 전용 화면으로 가는 링크.

    채팅방(+떠다니는 PIP)이 아니라 전용 경로로 보낸다. "빙고 하자"를 눌러 들어온
    사람에게 채팅방 위 작은 창을 주면 정작 하러 온 것이 곁다리로 보인다.
    채널과 실시간 연결은 채팅방과 같아서, 웹에서 PIP로 하는 사람과 같은 판에서 만난다.
    """
    token = create_access_token(
        str(user.id), user.token_version, expire_minutes=LINK_TOKEN_EXPIRE_MINUTES
    )
    base = settings.public_web_url.rstrip("/")
    path = f"{base}/servers/{channel.server_id}/channels/{channel.id}"
    # 전용 화면이 없는 항목(채팅)은 채널 화면 자체가 목적지다.
    if feature.page:
        path += f"/play/{feature.page}"
    return f"{path}?{urlencode({'t': token})}"
