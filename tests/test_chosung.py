import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.services.chosung.logic import WORDS, initials, is_hangul_word
from app.services.chosung.store import ChosungStore, get_chosung_store


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def _matching_word(prompt: str) -> str:
    """WORDS에서 주어진 초성 프롬프트와 정확히 일치하는 단어를 찾는다.

    random_prompt()가 WORDS 중 하나의 초성으로 프롬프트를 만들므로, 항상 매치가
    존재한다(문제는 반드시 답할 수 있게 설계됨). 실행마다 프롬프트가 랜덤이라
    하드코딩된 단어 대신 이렇게 찾아 써야 테스트가 결정적으로 통과한다.
    """
    return next(w for w in WORDS if initials(w) == prompt)


def _mismatching_word(prompt: str) -> str:
    """주어진 프롬프트와 초성이 다른(같은 길이의) WORDS 단어를 찾는다."""
    return next(w for w in WORDS if len(w) == len(prompt) and initials(w) != prompt)


# ---------- 스토어 로직 ----------


async def test_start_sets_playing_and_first_prompt():
    clock = FakeClock()
    store = ChosungStore(clock=clock)
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    game = await store.start(1, 1)
    assert game.status == "playing"
    assert game.prompt is not None
    assert game.turn_pos == 0  # 첫 참가자(Alice)부터 시작


async def test_submit_correct_word_advances_bomb_to_next_player():
    clock = FakeClock()
    store = ChosungStore(clock=clock)
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    game = await store.start(1, 1)
    word = _matching_word(game.prompt)

    game = await store.submit(1, 1, word)
    assert game.words == [word]
    assert word in game.used
    # 정답을 맞히면 폭탄이 다음 사람(Bob)에게 넘어가고 새 문제가 나온다.
    assert game.current_player().user_id == 2
    assert game.prompt is not None


async def test_submit_initials_mismatch_422():
    clock = FakeClock()
    store = ChosungStore(clock=clock)
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    game = await store.start(1, 1)
    wrong = _mismatching_word(game.prompt)

    with pytest.raises(HTTPException) as exc:
        await store.submit(1, 1, wrong)
    assert exc.value.status_code == 422


async def test_submit_non_hangul_word_422():
    clock = FakeClock()
    store = ChosungStore(clock=clock)
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    await store.start(1, 1)

    with pytest.raises(HTTPException) as exc:
        await store.submit(1, 1, "apple")
    assert exc.value.status_code == 422


async def test_duplicate_word_rejected_422():
    clock = FakeClock()
    store = ChosungStore(clock=clock)
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    game = await store.start(1, 1)
    word = _matching_word(game.prompt)
    game = await store.submit(1, 1, word)  # 폭탄 → Bob, 새 문제 출제

    # 다음 문제도 우연히 같은 단어로 풀리도록, 새 프롬프트에 맞는 단어를 찾되
    # 이미 나온 단어인지는 store가 초성 일치 검사보다 뒤에 판정하므로, 같은
    # 단어가 다시 정답이 되는 경우만 "중복" 케이스를 재현할 수 있다. 여기서는
    # 새 프롬프트가 우연히 같은 단어를 요구하지 않을 수도 있으므로, 직접
    # game.prompt를 이전 단어의 초성으로 맞춰 결정적으로 재현한다.
    game.prompt = initials(word)
    await store._save(game)

    with pytest.raises(HTTPException) as exc:
        await store.submit(1, 2, word)
    assert exc.value.status_code == 422


async def test_submit_out_of_turn_409():
    clock = FakeClock()
    store = ChosungStore(clock=clock)
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    game = await store.start(1, 1)
    word = _matching_word(game.prompt)

    # 지금은 Alice(1) 차례인데 Bob(2)이 제출하면 거절된다.
    with pytest.raises(HTTPException) as exc:
        await store.submit(1, 2, word)
    assert exc.value.status_code == 409


async def test_fuse_explodes_on_current_bomb_holder():
    clock = FakeClock()
    store = ChosungStore(fuse_seconds=120, clock=clock)
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    game = await store.start(1, 1)
    word = _matching_word(game.prompt)
    await store.submit(1, 1, word)  # 폭탄이 Bob에게 넘어감 (도화선은 그대로 유지)

    clock.now += 121  # 판 전체 단일 도화선 소진
    game, changed = await store.get(1)
    assert changed
    assert game.status == "finished"
    assert game.loser_user_id == 2  # 폭탄 든 Bob이 단일 패자
    bob = game.find_player(2)
    assert bob is not None and not bob.alive
    # 나머지는 살아있다 (누적 탈락 없음)
    assert game.find_player(1).alive


async def test_start_requires_two_players():
    clock = FakeClock()
    store = ChosungStore(clock=clock)
    await store.join(1, 1, "Alice")
    with pytest.raises(HTTPException) as exc:
        await store.start(1, 1)
    assert exc.value.status_code == 409


async def test_join_after_finish_opens_new_round():
    clock = FakeClock()
    store = ChosungStore(fuse_seconds=120, clock=clock)
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    await store.start(1, 1)
    clock.now += 121  # 도화선 소진 → 첫 폭탄 보유자 Alice 패배
    game, _ = await store.get(1)
    assert game.status == "finished"

    game = await store.join(1, 1, "Alice")
    assert game.round == 2
    assert game.status == "waiting"
    assert [p.user_id for p in game.players] == [1]


# ---------- 단어 규칙 (logic.py) ----------


def test_is_hangul_word_length_bounds():
    assert is_hangul_word("사과")
    assert is_hangul_word("바나나")
    assert not is_hangul_word("가")  # 한 글자
    assert not is_hangul_word("가나다라")  # 4글자
    assert not is_hangul_word("apple")


def test_initials_extracts_leading_consonants():
    assert initials("사과") == "ㅅㄱ"
    assert initials("바나나") == "ㅂㄴㄴ"


# ---------- API 플로우 ----------


@pytest.fixture
def fresh_chosung_store():
    from app.main import app

    store = ChosungStore()
    app.dependency_overrides[get_chosung_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_chosung_store, None)


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


async def test_api_full_flow(client: AsyncClient, register, fresh_chosung_store):
    token_a, token_b, channel_id = await _setup_channel(client, register)

    joined = await client.post(f"/channels/{channel_id}/chosung/join", headers=_headers(token_a))
    assert joined.status_code == 200
    assert joined.json()["status"] == "waiting"

    await client.post(f"/channels/{channel_id}/chosung/join", headers=_headers(token_b))
    started = await client.post(
        f"/channels/{channel_id}/chosung/start", headers=_headers(token_a)
    )
    assert started.status_code == 200
    state = started.json()
    assert state["status"] == "playing"
    assert state["prompt"] is not None

    word = _matching_word(state["prompt"])
    ok = await client.post(
        f"/channels/{channel_id}/chosung/submit",
        json={"word": word},
        headers=_headers(token_a),
    )
    assert ok.status_code == 200
    assert ok.json()["words"] == [word]

    fetched = await client.get(f"/channels/{channel_id}/chosung", headers=_headers(token_b))
    assert fetched.status_code == 200
    assert fetched.json()["turn_user_id"] == ok.json()["turn_user_id"]


async def test_api_non_member_403(client: AsyncClient, register, fresh_chosung_store):
    _token_a, _token_b, channel_id = await _setup_channel(client, register)
    token_c = await register(client, "c@test.com", "pass1234", "Carol")
    resp = await client.post(f"/channels/{channel_id}/chosung/join", headers=_headers(token_c))
    assert resp.status_code == 403
