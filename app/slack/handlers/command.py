"""`/ieum` 슬래시 커맨드 디스패치.

`command_parser.parse_command`로 텍스트를 분류한 뒤, 서브커맨드별로 알맞은
슬랙 응답(도움말·카탈로그·모달·기능 초대 등)을 고른다. 실제 DB·링크 발급은
`app.slack.identity`에 있고, 여기서는 라우팅만 담당한다.
"""

from slack_bolt.async_app import AsyncApp

from app.slack import blocks
from app.slack.command_parser import (
    HELP_TEXT,
    parse_command,
    resolve_feature,
    suggestions_for,
    unknown_reply,
)


def register_command(app: AsyncApp) -> None:
    @app.command("/ieum")
    async def handle_ieum(ack, command, client) -> None:
        parsed = parse_command(command.get("text"))

        if parsed.name == "ping":
            await ack(text="퐁 🏓")
            return
        if parsed.name == "help":
            await ack(text=HELP_TEXT)
            return
        if parsed.name == "catalog":
            await ack(blocks=blocks.catalog_blocks(), text="열 수 있는 것들")
            return

        # 태그 등록은 모달이다. trigger_id는 3초 만에 만료되므로 다른 일을 하기 전에
        # 곧바로 views.open을 호출한다 — DB부터 다녀오면 그 사이에 만료된다.
        if parsed.name == "tag_edit":
            await ack()
            await client.views_open(
                trigger_id=command["trigger_id"],
                view=blocks.tag_modal(command.get("channel_id", "")),
            )
            return

        # 남의 태그 보기 / AI 질문 — 둘 다 "누구를?"부터 물어야 한다.
        if parsed.name == "tag_view":
            await ack(
                blocks=blocks.user_pick_blocks(
                    blocks.PICK_TAGS_ACTION_ID, "누구의 관심사를 볼까요?"
                ),
                text="사람 고르기",
            )
            return
        if parsed.name == "icebreaker":
            await ack(
                blocks=blocks.user_pick_blocks(
                    blocks.PICK_ICEBREAKER_ACTION_ID, "누구에게 말을 걸어볼까요?"
                ),
                text="사람 고르기",
            )
            return

        feature = resolve_feature(parsed)
        if feature is None:
            # 이름을 안 적었으면(`/ieum 게임`) 타이핑 자동완성 선택기를 띄우고,
            # 뭔가 적었는데 못 찾았으면 가까운 후보를 버튼으로 되묻는다.
            # 도움말 전문을 던지는 것은 둘 다 아닐 때의 마지막 수단이다.
            if parsed.name == "open" and not parsed.args:
                await ack(
                    blocks=blocks.feature_pick_blocks(),
                    text="무엇을 열까요?",
                )
                return
            near = suggestions_for(parsed)
            if near:
                await ack(
                    blocks=blocks.suggestion_blocks(parsed.raw, near),
                    text="비슷한 것을 찾았어요",
                )
                return
            await ack(text=unknown_reply(parsed))
            return

        # 채널 전체에 보이는 초대 메시지. 링크는 없고 버튼만 있다.
        await ack(
            blocks=blocks.invite_blocks(feature, command.get("user_id", "")),
            text=f"{feature.label} 열림",
            response_type="in_channel",
        )
