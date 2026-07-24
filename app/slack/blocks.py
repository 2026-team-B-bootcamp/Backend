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


# ── 슬랙 안에서 끝나는 것들 (태그 · AI) ──────────────────────────────
#
# 이쪽은 링크로 내보내지 않는다. 슬랙에서 대화 중인 사람을 태그 세 개 적자고
# 브라우저로 쫓아내면 그 자리에서 흐름이 끊긴다.

TAG_MODAL_CALLBACK_ID = "ieum_tag_modal"
PICK_TAGS_ACTION_ID = "ieum_pick_tags"
PICK_ICEBREAKER_ACTION_ID = "ieum_pick_icebreaker"

# 태그 개수는 웹(TagSetupModal)과 같은 3개로 맞춘다 — 저장 구조가 tag1~tag3이다.
TAG_FIELDS = (
    ("tag1", "첫 번째 관심사", "예: 롤 티어 실버4"),
    ("tag2", "두 번째 관심사", "예: 라멘 맛집 탐방"),
    ("tag3", "세 번째 관심사", "예: 주말엔 클라이밍"),
)


def tag_modal(channel_id: str, current: tuple[str, str, str] = ("", "", "")) -> dict:
    """관심사 태그를 적는 모달.

    private_metadata에 채널을 실어 보낸다 — view_submission에는 채널 정보가
    없어서, 어느 워크스페이스·채널의 서버에 저장할지 알 수 없기 때문이다.
    """
    return {
        "type": "modal",
        "callback_id": TAG_MODAL_CALLBACK_ID,
        "private_metadata": channel_id,
        "title": {"type": "plain_text", "text": "관심사 태그"},
        "submit": {"type": "plain_text", "text": "저장"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": [
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            "겹치는 관심사를 가진 사람을 찾아주고, "
                            "AI가 말 걸 질문도 만들어줘요."
                        ),
                    }
                ],
            },
            *[
                {
                    "type": "input",
                    "block_id": field,
                    # 세 칸을 다 채우도록 강요하지 않는다 — 하나만 적어도 매칭은 된다.
                    "optional": True,
                    "label": {"type": "plain_text", "text": label},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "value",
                        "max_length": 40,
                        "initial_value": value,
                        "placeholder": {"type": "plain_text", "text": placeholder},
                    },
                }
                for (field, label, placeholder), value in zip(TAG_FIELDS, current, strict=True)
            ],
        ],
    }


def user_pick_blocks(action_id: str, prompt: str) -> list[dict]:
    """사람을 고르는 선택기.

    이름을 타이핑하게 하지 않는 이유: 슬래시 커맨드 텍스트에는 유저 ID가 실려오지
    않아(should_escape=false) `@민수`가 그냥 글자로 온다. 이름으로 사람을 찾으면
    동명이인·표시이름 변경에 그대로 깨진다. 슬랙 기본 선택기는 ID를 준다.
    """
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": prompt},
            "accessory": {
                "type": "users_select",
                "action_id": action_id,
                "placeholder": {"type": "plain_text", "text": "사람 고르기"},
            },
        }
    ]


def tags_card_blocks(display_name: str, tags: list[str], mine: bool) -> list[dict]:
    """누군가의 관심사 태그 카드."""
    if not tags:
        text = (
            "아직 관심사를 등록하지 않으셨어요. `/ieum 태그등록` 으로 적어보세요."
            if mine
            else f"*{display_name}* 님은 아직 관심사를 등록하지 않았어요."
        )
        return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]

    pills = "  ".join(f"`{t}`" for t in tags)
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"🏷️ *{display_name}* 님의 관심사\n{pills}"},
        }
    ]


def icebreaker_blocks(target_name: str, questions: list[str]) -> list[dict]:
    """AI가 만든 말 걸기 질문들.

    고르라고 여러 개를 준다 — 하나만 주면 마음에 안 들 때 다시 부르는 수밖에 없다.
    """
    if not questions:
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{target_name}* 님의 관심사가 없어서 질문을 만들지 못했어요.",
                },
            }
        ]
    body = "\n".join(f"• {q}" for q in questions)
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"💬 *{target_name}* 님에게 말 걸기\n{body}"},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "마음에 드는 걸 골라 그대로 보내보세요 (나에게만 보임)"}
            ],
        },
    ]
