"""슬랙 안에서 끝나는 것들 — 태그 등록·조회, AI 말걸어줘.

이쪽은 웹으로 내보내지 않으므로 링크가 아니라 "슬랙 계정 → 이음 태그"가 실제로
이어지는지가 관건이다. 슬랙에서 적은 태그가 웹에서도 보여야 같은 사람이다.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import tag_service
from app.slack import blocks
from app.slack.handlers import _load_tags, _resolve_target

TEAM = {"id": "T0TEST", "domain": "우리팀"}
CHANNEL = {"id": "C0TEST", "name": "일반"}
USER = "U0TEST"


class _FakeSlackClient:
    def __init__(self, names: dict[str, str] | None = None):
        self._names = names or {}

    async def users_info(self, user: str):
        return {"user": {"profile": {"display_name": self._names.get(user, user)}}}


@pytest_asyncio.fixture
async def slack_session(db_engine, monkeypatch):
    monkeypatch.setattr(
        "app.db.base.async_session_maker",
        async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False),
    )


# ── 태그 ─────────────────────────────────────────────────────────────


async def test_tags_written_from_slack_are_readable(slack_session, db_engine):
    """슬랙에서 적은 태그가 그대로 조회돼야 한다."""
    client = _FakeSlackClient({USER: "박민수"})
    user_id, name, server_id = await _resolve_target(client, TEAM, CHANNEL, USER)
    assert name == "박민수"

    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        await tag_service.upsert_tags(db, server_id, user_id, "롤 실버4", "라멘 탐방", "")

    assert await _load_tags(server_id, user_id) == ["롤 실버4", "라멘 탐방"]


async def test_empty_tags_are_dropped(slack_session, db_engine):
    """모달의 빈 칸이 빈 문자열 태그로 남으면 안 된다."""
    client = _FakeSlackClient()
    user_id, _, server_id = await _resolve_target(client, TEAM, CHANNEL, USER)

    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        await tag_service.upsert_tags(db, server_id, user_id, "  ", "", "클라이밍")

    assert await _load_tags(server_id, user_id) == ["클라이밍"]


async def test_no_tags_returns_empty(slack_session):
    client = _FakeSlackClient()
    user_id, _, server_id = await _resolve_target(client, TEAM, CHANNEL, USER)
    assert await _load_tags(server_id, user_id) == []


async def test_resolving_the_same_person_twice_is_one_account(slack_session):
    """태그를 적은 사람과 나중에 조회되는 사람이 같아야 한다."""
    client = _FakeSlackClient({USER: "박민수"})
    first = await _resolve_target(client, TEAM, CHANNEL, USER)
    second = await _resolve_target(client, TEAM, CHANNEL, USER)
    assert first == second


async def test_resolving_someone_who_never_used_the_bot(slack_session):
    """봇을 한 번도 안 쓴 사람도 조회 대상이 될 수 있어야 한다.

    그래야 "저 사람 관심사 뭐지?"를 먼저 물어볼 수 있고, 나중에 그 사람이
    태그를 적으면 같은 계정에 붙는다.
    """
    client = _FakeSlackClient({"U0NEVER": "처음온사람"})
    user_id, name, server_id = await _resolve_target(client, TEAM, CHANNEL, "U0NEVER")
    assert name == "처음온사람"
    assert await _load_tags(server_id, user_id) == []


async def test_two_people_in_same_workspace_share_a_server(slack_session):
    """같은 워크스페이스면 같은 서버 — 그래야 서로의 태그가 보인다."""
    client = _FakeSlackClient()
    _, _, server_a = await _resolve_target(client, TEAM, CHANNEL, "U0AAA")
    _, _, server_b = await _resolve_target(client, TEAM, CHANNEL, "U0BBB")
    assert server_a == server_b


# ── 블록 모양 ────────────────────────────────────────────────────────


def test_tag_modal_carries_the_channel():
    """view_submission에는 채널이 안 실려온다 — 열 때 넣어둔 값이 유일한 단서다."""
    view = blocks.tag_modal("C0ABC")
    assert view["private_metadata"] == "C0ABC"
    assert view["callback_id"] == blocks.TAG_MODAL_CALLBACK_ID
    block_ids = [b["block_id"] for b in view["blocks"] if b["type"] == "input"]
    assert block_ids == ["tag1", "tag2", "tag3"]


def test_tag_modal_prefills_existing_tags():
    view = blocks.tag_modal("C0ABC", ("롤", "라멘", ""))
    inputs = [b for b in view["blocks"] if b["type"] == "input"]
    assert [b["element"]["initial_value"] for b in inputs] == ["롤", "라멘", ""]


def test_tag_modal_fields_are_optional():
    """세 칸을 다 채우라고 강요하면 하나만 있는 사람이 등록 자체를 포기한다."""
    view = blocks.tag_modal("C0ABC")
    assert all(b["optional"] for b in view["blocks"] if b["type"] == "input")


@pytest.mark.parametrize("mine", [True, False])
def test_tags_card_handles_empty(mine):
    out = blocks.tags_card_blocks("민수", [], mine=mine)
    text = out[0]["text"]["text"]
    # 본인에게는 등록 방법을, 남에게는 그 사람이 없다는 사실을 알린다
    assert ("태그등록" in text) is mine


def test_icebreaker_blocks_lists_every_question():
    out = blocks.icebreaker_blocks("민수", ["질문1", "질문2", "질문3"])
    body = out[0]["text"]["text"]
    assert "질문1" in body and "질문2" in body and "질문3" in body


def test_icebreaker_blocks_without_tags_explains_why():
    out = blocks.icebreaker_blocks("민수", [])
    assert "관심사" in out[0]["text"]["text"]


def test_user_pick_uses_slack_native_selector():
    """이름을 타이핑하게 하면 동명이인·개명에 깨진다. 슬랙 선택기는 ID를 준다."""
    out = blocks.user_pick_blocks(blocks.PICK_TAGS_ACTION_ID, "누구?")
    assert out[0]["accessory"]["type"] == "users_select"
    assert out[0]["accessory"]["action_id"] == blocks.PICK_TAGS_ACTION_ID
