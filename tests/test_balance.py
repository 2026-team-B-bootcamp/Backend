import time

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.core.redis import get_redis
from app.services.balance.store import (
    DURATION_SECONDS,
    TTL_SECONDS,
    BalanceGame,
    BalanceStore,
    get_balance_store,
)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _expire(store: BalanceStore, game: BalanceGame) -> None:
    """이미 마감된 것처럼 시각을 되돌려 Redis에 다시 저장한다.

    BalanceStore는 wordchain/omok과 달리 생성자에 가짜 시계를 주입할 수 없다
    (time.time()을 직접 호출). 대신 test_omok.py가 board를 직접 조작해 _save한
    방식과 동일하게, created_at을 과거로 되돌려 실제 sleep 없이 마감을 재현한다.
    """
    game.created_at = time.time() - DURATION_SECONDS - 1
    await store._save(game)


# ---------- 투표 ----------


async def test_vote_records_choice():
    store = BalanceStore()
    await store.start(1, "치킨", "피자", 1, "Alice")
    game = await store.vote(1, 2, "a")
    assert game.votes["2"] == "a"


async def test_revote_overwrites_previous_choice():
    store = BalanceStore()
    await store.start(1, "치킨", "피자", 1, "Alice")
    await store.vote(1, 2, "a")
    game = await store.vote(1, 2, "b")
    assert game.votes["2"] == "b"
    assert len(game.votes) == 1  # 새 항목이 추가되는 게 아니라 덮어쓴다


async def test_vote_after_deadline_rejected_409():
    store = BalanceStore()
    game = await store.start(1, "치킨", "피자", 1, "Alice")
    await _expire(store, game)
    with pytest.raises(HTTPException) as exc:
        await store.vote(1, 2, "a")
    assert exc.value.status_code == 409


# ---------- 댓글 ----------


async def test_comment_auto_attaches_current_vote_as_side():
    store = BalanceStore()
    await store.start(1, "치킨", "피자", 1, "Alice")
    await store.vote(1, 2, "b")
    game = await store.comment(1, 2, "Bob", "피자가 더 낫지 않나요")
    assert game.comments[0].side == "b"


async def test_comment_without_prior_vote_has_no_side():
    store = BalanceStore()
    await store.start(1, "치킨", "피자", 1, "Alice")
    game = await store.comment(1, 3, "Carol", "저는 둘 다 좋아요")
    assert game.comments[0].side is None


async def test_comment_after_deadline_rejected_409():
    store = BalanceStore()
    game = await store.start(1, "치킨", "피자", 1, "Alice")
    await _expire(store, game)
    with pytest.raises(HTTPException) as exc:
        await store.comment(1, 2, "Bob", "이미 끝났는데요")
    assert exc.value.status_code == 409


# ---------- 시작/재시작 ----------


async def test_restart_while_active_rejected_409():
    store = BalanceStore()
    await store.start(1, "치킨", "피자", 1, "Alice")
    with pytest.raises(HTTPException) as exc:
        await store.start(1, "탕수육", "짜장면", 2, "Bob")
    assert exc.value.status_code == 409


async def test_restart_allowed_after_deadline():
    store = BalanceStore()
    game = await store.start(1, "치킨", "피자", 1, "Alice")
    await _expire(store, game)
    # 마감된 판은 아무나 새로 시작할 수 있다(기존 결과를 덮어씀).
    new_game = await store.start(1, "탕수육", "짜장면", 2, "Bob")
    assert new_game.option_a == "탕수육"
    assert new_game.host_user_id == 2


# ---------- is_finished 경계 ----------


def test_is_finished_boundary():
    game = BalanceGame(
        channel_id=1,
        option_a="치킨",
        option_b="피자",
        host_user_id=1,
        host_name="Alice",
        created_at=1000.0,
    )
    assert not game.is_finished(1000.0 + DURATION_SECONDS - 1)
    # >= 비교이므로 정확히 마감 시각에 이미 종료로 판정된다.
    assert game.is_finished(1000.0 + DURATION_SECONDS)


# ---------- TTL (의도된 고정값) ----------


async def test_ttl_is_fixed_regardless_of_status():
    """balance/store.py는 다른 5개 스토어(ttl_for 사용)와 달리 ex=int(self._ttl)로
    고정 TTL(기본 2100초 = 5분 진행 + 30분 여유)을 쓴다. 마감 후에도 결과를 잠시
    볼 수 있게 하려는 의도된 동작(store.py:73 주석)이므로, 통합 리팩토링 때
    ttl_for()로 바뀌어 결과가 30초 만에 사라지는 회귀를 이 테스트가 잡는다.
    """
    store = BalanceStore()
    await store.start(1, "치킨", "피자", 1, "Alice")
    ttl_after_start = await get_redis().ttl("game:balance:1")
    await store.vote(1, 2, "a")
    ttl_after_vote = await get_redis().ttl("game:balance:1")

    assert ttl_after_start == TTL_SECONDS == 2100
    assert ttl_after_vote == TTL_SECONDS


# ---------- API 플로우 ----------


@pytest.fixture
def fresh_balance_store():
    from app.main import app

    store = BalanceStore()
    app.dependency_overrides[get_balance_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_balance_store, None)


async def _setup_channel(client: AsyncClient, register):
    token_a = await register(client, "a@test.com", "pass1234", "Alice")
    token_b = await register(client, "b@test.com", "pass1234", "Bob")
    server = (
        await client.post("/servers", json={"name": "Team"}, headers=_headers(token_a))
    ).json()
    await client.post(
        "/servers/join",
        json={"invite_code": server["invite_code"]},
        headers=_headers(token_b),
    )
    channels = (
        await client.get(f"/servers/{server['id']}/channels", headers=_headers(token_a))
    ).json()
    return token_a, token_b, channels[0]["id"]


async def test_api_full_flow(client: AsyncClient, register, fresh_balance_store):
    token_a, token_b, channel_id = await _setup_channel(client, register)

    started = await client.post(
        f"/channels/{channel_id}/balance/start",
        json={"option_a": "치킨", "option_b": "피자"},
        headers=_headers(token_a),
    )
    assert started.status_code == 200
    state = started.json()
    assert state["active"] is True
    assert state["option_a"] == "치킨"

    voted = await client.post(
        f"/channels/{channel_id}/balance/vote",
        json={"side": "b"},
        headers=_headers(token_b),
    )
    assert voted.status_code == 200
    assert voted.json()["count_b"] == 1

    commented = await client.post(
        f"/channels/{channel_id}/balance/comment",
        json={"text": "피자 승"},
        headers=_headers(token_b),
    )
    assert commented.status_code == 200
    assert commented.json()["comments"][0]["side"] == "b"

    fetched = await client.get(f"/channels/{channel_id}/balance", headers=_headers(token_a))
    assert fetched.status_code == 200
    assert fetched.json()["count_b"] == 1


async def test_api_non_member_403(client: AsyncClient, register, fresh_balance_store):
    _token_a, _token_b, channel_id = await _setup_channel(client, register)
    token_c = await register(client, "c@test.com", "pass1234", "Carol")
    resp = await client.post(
        f"/channels/{channel_id}/balance/start",
        json={"option_a": "치킨", "option_b": "피자"},
        headers=_headers(token_c),
    )
    assert resp.status_code == 403
