import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.services.wordchain.logic import allowed_first_chars, is_hangul_word
from app.services.wordchain.store import WordChainStore, get_wordchain_store


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


# ---------- 단어 규칙 ----------


def test_is_hangul_word():
    assert is_hangul_word("사과")
    assert is_hangul_word("끝말잇기")
    assert not is_hangul_word("사")  # 한 글자
    assert not is_hangul_word("apple")
    assert not is_hangul_word("사과1")
    assert not is_hangul_word("가" * 11)  # 너무 긺


def test_allowed_first_chars_plain():
    assert allowed_first_chars("사과") == {"과"}


def test_allowed_first_chars_dueum_rieul():
    # ㄹ + 일반 모음 → ㄴ 허용 (도라 → 라/나)
    assert allowed_first_chars("도라") == {"라", "나"}
    # ㄹ + 이 계열 모음 → ㄴ/ㅇ 허용 (요리 → 리/니/이)
    assert allowed_first_chars("요리") == {"리", "니", "이"}
    # 받침 유지 (실력 → 력/녁/역)
    assert allowed_first_chars("실력") == {"력", "녁", "역"}


def test_allowed_first_chars_dueum_nieun():
    # ㄴ + 이 계열 모음 → ㅇ 허용 (어머니 → 니/이)
    assert allowed_first_chars("어머니") == {"니", "이"}
    # ㄴ + 일반 모음은 그대로 (바나 → 나)
    assert allowed_first_chars("바나") == {"나"}


# ---------- 스토어 로직 (가짜 시계) ----------


async def test_full_round_and_chain_rule():
    clock = FakeClock()
    store = WordChainStore(clock=clock)
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    game = await store.start(1, 1)
    assert game.status == "playing"

    await store.submit(1, 1, "사과")
    # Bob 차례: '과'로 시작해야 한다
    with pytest.raises(HTTPException) as exc:
        await store.submit(1, 2, "바나나")
    assert exc.value.status_code == 422

    game = await store.submit(1, 2, "과일")
    assert [w.word for w in game.words] == ["사과", "과일"]


async def test_out_of_turn_409():
    clock = FakeClock()
    store = WordChainStore(clock=clock)
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    await store.start(1, 1)
    with pytest.raises(HTTPException) as exc:
        await store.submit(1, 2, "사과")
    assert exc.value.status_code == 409


async def test_duplicate_word_rejected():
    clock = FakeClock()
    store = WordChainStore(clock=clock)
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    await store.start(1, 1)
    await store.submit(1, 1, "사과")
    await store.submit(1, 2, "과사")  # 다시 Alice 차례, '사'로 시작해야 함
    with pytest.raises(HTTPException) as exc:
        await store.submit(1, 1, "사과")  # 이미 나온 단어
    assert exc.value.status_code == 422


async def test_timeout_eliminates_and_finishes():
    clock = FakeClock()
    store = WordChainStore(turn_seconds=30, clock=clock)
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    await store.start(1, 1)
    await store.submit(1, 1, "사과")  # 이제 Bob 차례

    clock.now += 31  # Bob 시간 초과
    game, changed = await store.get(1)
    assert changed
    assert game.status == "finished"
    assert game.winner_user_id == 1
    bob = game.find_player(2)
    assert bob is not None and not bob.alive


async def test_timeout_passes_turn_with_three_players():
    clock = FakeClock()
    store = WordChainStore(turn_seconds=30, clock=clock)
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    await store.join(1, 3, "Carol")
    await store.start(1, 1)
    await store.submit(1, 1, "사과")  # Bob 차례

    clock.now += 31  # Bob 탈락 → Carol 차례, 게임은 계속
    game, changed = await store.get(1)
    assert changed
    assert game.status == "playing"
    assert game.current_player().user_id == 3
    assert not game.find_player(2).alive


async def test_join_after_finish_opens_new_round():
    clock = FakeClock()
    store = WordChainStore(turn_seconds=30, clock=clock)
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    await store.start(1, 1)
    clock.now += 31  # Alice(첫 차례) 시간 초과 → Bob 승리
    game, _ = await store.get(1)
    assert game.status == "finished"

    game = await store.join(1, 1, "Alice")
    assert game.round == 2
    assert game.status == "waiting"
    assert [p.user_id for p in game.players] == [1]


async def test_start_requires_two_players():
    clock = FakeClock()
    store = WordChainStore(clock=clock)
    await store.join(1, 1, "Alice")
    with pytest.raises(HTTPException) as exc:
        await store.start(1, 1)
    assert exc.value.status_code == 409


# ---------- API 플로우 ----------


@pytest.fixture
def fresh_wc_store():
    from app.main import app

    store = WordChainStore()
    app.dependency_overrides[get_wordchain_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_wordchain_store, None)


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


async def test_api_full_flow(client: AsyncClient, register, fresh_wc_store):
    token_a, token_b, channel_id = await _setup_channel(client, register)

    joined = await client.post(
        f"/channels/{channel_id}/wordchain/join", headers=_headers(token_a)
    )
    assert joined.status_code == 200
    assert joined.json()["status"] == "waiting"

    await client.post(f"/channels/{channel_id}/wordchain/join", headers=_headers(token_b))
    started = await client.post(
        f"/channels/{channel_id}/wordchain/start", headers=_headers(token_a)
    )
    assert started.status_code == 200
    state = started.json()
    assert state["status"] == "playing"
    assert state["seconds_left"] is not None

    ok = await client.post(
        f"/channels/{channel_id}/wordchain/submit",
        json={"word": "사과"},
        headers=_headers(token_a),
    )
    assert ok.status_code == 200
    assert [w["word"] for w in ok.json()["words"]] == ["사과"]

    bad = await client.post(
        f"/channels/{channel_id}/wordchain/submit",
        json={"word": "바나나"},
        headers=_headers(token_b),
    )
    assert bad.status_code == 422

    fetched = await client.get(
        f"/channels/{channel_id}/wordchain", headers=_headers(token_b)
    )
    assert fetched.status_code == 200
    assert fetched.json()["turn_user_id"] == ok.json()["turn_user_id"]


async def test_api_non_member_403(client: AsyncClient, register, fresh_wc_store):
    _token_a, _token_b, channel_id = await _setup_channel(client, register)
    token_c = await register(client, "c@test.com", "pass1234", "Carol")
    resp = await client.post(
        f"/channels/{channel_id}/wordchain/join", headers=_headers(token_c)
    )
    assert resp.status_code == 403
