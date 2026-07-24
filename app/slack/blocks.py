"""Block Kit 메시지 조립.

메시지 모양만 담당하고 DB·토큰은 모르는 순수 함수들이다 — 눈으로 확인해야 하는
부분이라 테스트로 구조만 고정해두고, 문구는 자유롭게 고칠 수 있게 분리했다.
"""

from app.slack.features import GAMES, TOOLS, Feature

# 버튼 클릭을 받는 핸들러 식별자. handlers.py의 @app.action 과 짝이다.
JOIN_ACTION_ID = "ieum_join"
# url 버튼도 누르면 인터랙션이 날아온다. 처리할 일은 없지만 받아서 ack해야
# Bolt가 "unhandled request" 경고를 남기지 않는다.
OPEN_LINK_ACTION_ID = "ieum_open_link"


def invite_blocks(feature: Feature, opener_user_id: str) -> list[dict]:
    """채널에 공개로 올라가는 초대 메시지. **링크를 담지 않는다.**

    링크를 여기 넣으면 채널의 아무나(심지어 외부로 전달받은 사람도) 그 사람
    행세를 할 수 있다. 버튼만 두고, 누른 사람에게 개인 링크를 따로 준다.
    """
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{feature.emoji} *{feature.label}* 열렸어요!\n"
                    f"<@{opener_user_id}>님이 시작했습니다. 참여하려면 버튼을 누르세요."
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "참여하기", "emoji": True},
                    "style": "primary",
                    "action_id": JOIN_ACTION_ID,
                    "value": feature.key,
                }
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "버튼을 누르면 본인만 볼 수 있는 입장 링크를 보내드려요 (15분 유효)",
                }
            ],
        },
    ]


def entry_link_blocks(feature: Feature, link: str) -> list[dict]:
    """버튼을 누른 본인에게만 보이는 에페메랄 응답."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{feature.emoji} *{feature.label}* 입장 링크입니다.",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "입장하기", "emoji": True},
                    "style": "primary",
                    "action_id": OPEN_LINK_ACTION_ID,
                    "url": link,
                }
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "이 링크는 *회원님 전용*이고 15분 뒤 만료됩니다. "
                        "다시 필요하면 위 [참여하기]를 다시 누르세요."
                    ),
                }
            ],
        },
    ]


def catalog_blocks() -> list[dict]:
    """`/ieum 목록` — 열 수 있는 것들을 버튼으로 전부 보여준다."""

    def buttons(features: tuple[Feature, ...]) -> list[dict]:
        return [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": f"{f.emoji} {f.label}",
                    "emoji": True,
                },
                "action_id": f"{JOIN_ACTION_ID}_{f.key}",
                "value": f.key,
            }
            for f in features
        ]

    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🎮 게임*"},
        },
        # actions 블록의 요소는 최대 25개라 6개·4개는 여유롭게 들어간다.
        {"type": "actions", "elements": buttons(GAMES)},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🧰 그 외*"},
        },
        {"type": "actions", "elements": buttons(TOOLS)},
    ]
