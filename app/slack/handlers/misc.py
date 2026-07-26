"""나머지 잡다한 핸들러 — url 버튼 ack, 멘션 응답."""

from slack_bolt.async_app import AsyncApp

from app.slack import blocks
from app.slack.features import catalog_text


def register_misc(app: AsyncApp) -> None:
    @app.action(blocks.OPEN_LINK_ACTION_ID)
    async def handle_open_link(ack) -> None:
        # url 버튼은 슬랙이 알아서 브라우저를 여니 우리가 할 일은 없다. ack만 한다.
        await ack()

    @app.event("app_mention")
    async def handle_app_mention(event, say) -> None:
        # 스레드 안에서 멘션되면 같은 스레드로, 아니면 그 메시지에 스레드를 열어 답한다
        # — 채널 타임라인을 봇 응답으로 어지럽히지 않기 위해서다.
        await say(
            text=f"안녕하세요! `/ieum 목록`으로 시작해보세요.\n\n{catalog_text()}",
            thread_ts=event.get("thread_ts") or event.get("ts"),
        )
