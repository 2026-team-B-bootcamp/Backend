"""태그 등록 모달과 태그 조회 / AI 아이스브레이커 액션.

`/ieum 태그등록` 모달 제출, "누군가의 관심사 보기", "AI가 질문 만들어줌" 세
흐름을 담는다. 셋 다 슬랙 유저를 Deverapo 계정으로 옮기는
`app.slack.identity._resolve_target`을 먼저 거친 뒤 태그 서비스나 AI를 부른다.
"""

import logging

from slack_bolt.async_app import AsyncApp

from app.db import base as db_base
from app.services import tag_service
from app.services.ai import service as ai_service
from app.services.ai.provider import get_icebreaker_provider
from app.slack import blocks
from app.slack.identity import _load_tags, _resolve_target

logger = logging.getLogger(__name__)


def register_tags_ai(app: AsyncApp) -> None:
    @app.view(blocks.TAG_MODAL_CALLBACK_ID)
    async def handle_tag_submit(ack, body, view, client) -> None:
        values = view["state"]["values"]
        tags = [
            (values.get(field, {}).get("value", {}).get("value") or "").strip()[:40]
            for field, _, _ in blocks.TAG_FIELDS
        ]
        slack_user_id = body["user"]["id"]
        # 모달에는 채널이 실려오지 않아 열 때 private_metadata에 담아뒀다.
        channel_id = view.get("private_metadata") or ""

        try:
            user_id, _, server_id = await _resolve_target(
                client, body.get("team") or {}, {"id": channel_id}, slack_user_id
            )
            async with db_base.async_session_maker() as db:
                await tag_service.upsert_tags(db, server_id, user_id, *tags)
        except Exception:
            logger.exception("태그 저장 실패 user=%s", slack_user_id)
            # 모달 위에 그대로 에러를 띄운다 — 닫아버리면 적은 내용이 날아간다.
            await ack(
                response_action="errors",
                errors={"tag1": "저장에 실패했어요. 잠시 후 다시 시도해주세요."},
            )
            return

        await ack()
        saved = [t for t in tags if t]
        text = (
            "관심사를 저장했어요: " + "  ".join(f"`{t}`" for t in saved)
            if saved
            else "관심사를 모두 지웠어요."
        )
        # 모달은 채널 맥락이 없어 respond()를 못 쓴다. 본인에게 DM으로 알린다.
        try:
            await client.chat_postEphemeral(
                channel=channel_id, user=slack_user_id, text=text
            )
        except Exception:
            logger.warning("태그 저장 알림 실패 — 저장 자체는 됐다", exc_info=True)

    @app.action(blocks.PICK_TAGS_ACTION_ID)
    async def handle_pick_tags(ack, body, client, respond) -> None:
        await ack()
        target = body["actions"][0]["selected_user"]
        me = body["user"]["id"]
        try:
            user_id, name, server_id = await _resolve_target(
                client, body.get("team") or {}, body.get("channel") or {}, target
            )
            tags = await _load_tags(server_id, user_id)
        except Exception:
            logger.exception("태그 조회 실패 target=%s", target)
            await respond(text="관심사를 불러오지 못했어요. 잠시 후 다시 시도해주세요.")
            return
        await respond(
            blocks=blocks.tags_card_blocks(name, tags, mine=target == me),
            text=f"{name} 님의 관심사",
            replace_original=True,
        )

    @app.action(blocks.PICK_ICEBREAKER_ACTION_ID)
    async def handle_pick_icebreaker(ack, body, client, respond) -> None:
        # AI 호출은 몇 초가 걸린다. 먼저 ack해서 슬랙의 3초 재전송을 막는다.
        await ack()
        target = body["actions"][0]["selected_user"]
        try:
            user_id, name, server_id = await _resolve_target(
                client, body.get("team") or {}, body.get("channel") or {}, target
            )
            tags = await _load_tags(server_id, user_id)
        except Exception:
            logger.exception("아이스브레이커 대상 조회 실패 target=%s", target)
            await respond(text="질문을 만들지 못했어요. 잠시 후 다시 시도해주세요.")
            return

        questions: list[str] = []
        if tags:
            try:
                questions = await ai_service.get_icebreakers(
                    get_icebreaker_provider(), name, tags
                )
            except Exception:
                logger.exception("아이스브레이커 생성 실패")
                await respond(text="AI가 질문을 만들지 못했어요. 잠시 후 다시 시도해주세요.")
                return

        await respond(
            blocks=blocks.icebreaker_blocks(name, questions),
            text=f"{name} 님에게 말 걸기",
            replace_original=True,
        )
