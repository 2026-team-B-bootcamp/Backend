"""기능 선택기(자동완성)와 "입장" 버튼 핸들러.

`/ieum 게임`처럼 이름 없이 연 선택기가 타이핑을 자동완성해주는 옵션 응답과,
그 선택기 또는 카탈로그에서 고른 기능을 채널 초대 메시지로 여는 액션,
그리고 초대 메시지의 "입장" 버튼을 눌렀을 때 개인 링크를 발급하는 액션을 담는다.
"""

import logging
import re

from slack_bolt.async_app import AsyncApp

from app.slack import blocks
from app.slack.features import by_key, search
from app.slack.identity import _issue_entry_link

logger = logging.getLogger(__name__)


def register_entry(app: AsyncApp) -> None:
    @app.options(blocks.FEATURE_SELECT_ACTION_ID)
    async def handle_feature_options(ack, payload) -> None:
        """선택기에 타이핑할 때마다 슬랙이 후보를 물어온다 — 그 응답.

        슬랙은 3초 안에 답을 못 받으면 "옵션을 불러오지 못했습니다"를 띄우므로
        DB·네트워크를 타지 않고 메모리의 카탈로그(features.py)만 읽는다.
        """
        typed = (payload or {}).get("value", "")
        await ack(options=[blocks.feature_option(f) for f in search(typed)])

    @app.action(blocks.FEATURE_SELECT_ACTION_ID)
    async def handle_feature_selected(ack, body, client, respond) -> None:
        """선택기에서 고른 기능을 채널 초대 메시지로 연다."""
        await ack()
        selected = (body.get("actions") or [{}])[0].get("selected_option") or {}
        feature = by_key(selected.get("value", ""))
        if feature is None:
            await respond(text="알 수 없는 항목이에요. `/ieum 목록`으로 다시 시도해주세요.")
            return

        channel_id = (body.get("channel") or {}).get("id", "")
        user_id = (body.get("user") or {}).get("id", "")
        try:
            # 선택기는 나에게만 보이는 메시지 안에 있다. 초대는 채널 전체가 봐야
            # 하므로 응답을 고쳐 쓰는 대신 채널에 새 메시지를 올린다.
            await client.chat_postMessage(
                channel=channel_id,
                blocks=blocks.invite_blocks(feature, user_id),
                text=f"{feature.label} 열림",
            )
        except Exception:
            logger.exception("선택기에서 연 초대 메시지 게시 실패 feature=%s", feature.key)
            await respond(text="열지 못했어요. 잠시 후 다시 시도해주세요.")
            return
        # 역할을 다한 선택기는 치운다 — 남겨두면 같은 걸 또 여는 실수를 부른다.
        await respond(text=f"{feature.emoji} {feature.label} 열었어요!", replace_original=True)

    # 초대 메시지는 `ieum_join` 하나지만, `/ieum 목록`은 버튼이 여러 개라
    # `ieum_join_<기능>`으로 갈린다 (한 메시지 안에서 action_id가 겹치면 안 된다).
    # 둘 다 같은 처리라 정규식 하나로 받는다.
    @app.action(re.compile(rf"^{blocks.JOIN_ACTION_ID}(_.+)?$"))
    async def handle_join(ack, body, client, respond) -> None:
        # 먼저 ack — 링크 발급(DB + users.info)이 3초를 넘길 여지를 없앤다.
        await ack()
        actions = body.get("actions") or [{}]
        feature = by_key(actions[0].get("value", ""))
        if feature is None:
            await respond(text="알 수 없는 항목이에요. `/ieum 목록`으로 다시 시도해주세요.")
            return

        try:
            link = await _issue_entry_link(client, body, feature)
        except Exception:
            logger.exception("입장 링크 발급 실패")
            await respond(text="입장 링크를 만들지 못했어요. 잠시 후 다시 눌러주세요.")
            return

        await respond(
            blocks=blocks.entry_link_blocks(feature, link),
            text=f"{feature.label} 입장 링크",
            replace_original=False,
        )
