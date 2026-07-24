"""슬랙 요청을 받는 FastAPI 라우터.

슬랙 앱 설정의 세 Request URL이 여기로 온다:
  POST /slack/events        Event Subscriptions (app_mention 등) + url_verification challenge
  POST /slack/commands      Slash Commands (/ieum …)
  POST /slack/interactions   Interactivity (버튼 클릭 등)

⚠️ 서명 검증은 **가공되지 않은 raw body**를 필요로 한다. 이 라우터보다 앞에서
body를 소비하는 미들웨어를 main.py에 두면 전 요청이 401로 떨어진다.
"""

import logging

from fastapi import APIRouter, Request
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.response import BoltResponse

from app.core.config import settings
from app.core.redis import get_redis
from app.slack import handlers

logger = logging.getLogger(__name__)

# 처리 완료된 이벤트 ID를 기억해두는 시간. 슬랙의 재전송은 최대 3회이고
# 마지막 재시도까지가 수 분 이내라 10분이면 충분하다.
_DEDUPE_TTL_SECONDS = 600


def _build_bolt_app() -> AsyncApp:
    bolt_app = AsyncApp(
        token=settings.slack_bot_token,
        signing_secret=settings.slack_signing_secret,
        # FastAPI가 라우팅을 맡으므로 Bolt 자체 HTTP 서버 기능은 쓰지 않는다.
        process_before_response=True,
        # request_verification_enabled / url_verification_enabled는 기본값 True라
        # 넘기지 않는다. 각각 서명 검증과 challenge 응답을 담당하며, 둘 다
        # tests/test_slack.py가 실제 요청으로 지키고 있다.
    )

    @bolt_app.middleware
    async def skip_duplicate_events(req, resp, next):  # noqa: A002 (Bolt가 정한 이름)
        """같은 이벤트를 두 번 처리하지 않게 막는다.

        슬랙은 3초 안에 200을 못 받으면 같은 이벤트를 다시 보낸다. 그대로 두면
        봇 응답이 채널에 두 번 게시된다. 두 겹으로 막는다:
        ① 재전송 헤더가 붙어 있으면 즉시 200만 돌려주고 끝낸다.
        ② 워커가 여러 개라도 event_id를 Redis에 SETNX로 선점한 요청만 처리한다.
        """
        retry_num = req.headers.get("x-slack-retry-num")
        if retry_num:
            logger.info("슬랙 재전송 무시 (retry-num=%s)", retry_num)
            return BoltResponse(status=200, body="")

        event_id = (req.body or {}).get("event_id") if isinstance(req.body, dict) else None
        if event_id:
            # Redis가 잠깐 죽어도 봇이 먹통이 되면 안 된다 — 중복 위험을 감수하고 통과시킨다.
            try:
                first = await get_redis().set(
                    f"slack:event:{event_id}", "1", nx=True, ex=_DEDUPE_TTL_SECONDS
                )
            except Exception:
                logger.warning("이벤트 중복 검사 실패 — 그대로 진행한다", exc_info=True)
            else:
                if not first:
                    logger.info("이미 처리한 이벤트 무시 (event_id=%s)", event_id)
                    return BoltResponse(status=200, body="")

        return await next()

    handlers.register(bolt_app)
    return bolt_app


def build_router() -> APIRouter:
    """슬랙용 APIRouter를 만든다. `settings.slack_enabled`일 때만 호출한다."""
    bolt_handler = AsyncSlackRequestHandler(_build_bolt_app())
    router = APIRouter(prefix="/slack", tags=["slack"])

    # 세 경로 모두 Bolt 핸들러 하나가 처리한다 — 서명 검증·페이로드 분기가 그 안에 있다.
    @router.post("/events")
    async def slack_events(req: Request):
        return await bolt_handler.handle(req)

    @router.post("/commands")
    async def slack_commands(req: Request):
        return await bolt_handler.handle(req)

    @router.post("/interactions")
    async def slack_interactions(req: Request):
        return await bolt_handler.handle(req)

    return router
