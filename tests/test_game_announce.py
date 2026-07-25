"""게임이 열리면 채팅에 입장 카드가 남는지 (kind="game").

핵심은 "새로 열렸을 때만" 남는다는 것이다. 두 번째 참가자까지 카드를 만들면
사람이 들어올 때마다 채팅이 카드로 도배된다.
"""

import pytest

GAMES_WITH_JOIN = ["bingo", "wordchain", "omok", "tictactoe", "chosung"]


async def _messages(client, headers, channel_id):
    res = await client.get(f"/channels/{channel_id}/messages", headers=headers)
    assert res.status_code == 200
    return res.json()


def _game_cards(messages):
    return [m for m in messages if m["kind"] == "game"]


@pytest.mark.parametrize("game", GAMES_WITH_JOIN)
async def test_opening_a_game_posts_a_card(client, register, game):
    token = await register(client, "a@example.com", "pass1234", "여는사람")
    headers = {"Authorization": f"Bearer {token}"}
    server = (await client.post("/servers", json={"name": "모임"}, headers=headers)).json()
    channels = (await client.get(f"/servers/{server['id']}/channels", headers=headers)).json()
    cid = channels[0]["id"]

    assert _game_cards(await _messages(client, headers, cid)) == []

    res = await client.post(f"/channels/{cid}/{game}/join", headers=headers)
    assert res.status_code == 200

    cards = _game_cards(await _messages(client, headers, cid))
    assert len(cards) == 1
    # content에는 게임 키만 담는다 — 문구는 프런트가 그릴 때 만든다.
    assert cards[0]["content"] == game
    assert cards[0]["display_name"] == "여는사람"


async def test_second_player_does_not_post_another_card(client, register):
    """이미 열린 판에 낀 사람은 카드를 만들지 않는다 — 채팅이 도배된다."""
    token_a = await register(client, "a@example.com", "pass1234", "A")
    ha = {"Authorization": f"Bearer {token_a}"}
    server = (await client.post("/servers", json={"name": "모임"}, headers=ha)).json()
    cid = (await client.get(f"/servers/{server['id']}/channels", headers=ha)).json()[0]["id"]

    token_b = await register(client, "b@example.com", "pass1234", "B")
    hb = {"Authorization": f"Bearer {token_b}"}
    await client.post(
        "/servers/join", json={"invite_code": server["invite_code"]}, headers=hb
    )

    await client.post(f"/channels/{cid}/bingo/join", headers=ha)
    await client.post(f"/channels/{cid}/bingo/join", headers=hb)
    # 같은 사람이 다시 눌러도 마찬가지
    await client.post(f"/channels/{cid}/bingo/join", headers=ha)

    assert len(_game_cards(await _messages(client, ha, cid))) == 1


async def test_different_games_get_their_own_cards(client, register):
    """게임 종류별로 각각 열리므로 카드도 종류마다 하나씩 나온다."""
    token = await register(client, "a@example.com", "pass1234", "A")
    headers = {"Authorization": f"Bearer {token}"}
    server = (await client.post("/servers", json={"name": "모임"}, headers=headers)).json()
    cid = (await client.get(f"/servers/{server['id']}/channels", headers=headers)).json()[0]["id"]

    await client.post(f"/channels/{cid}/bingo/join", headers=headers)
    await client.post(f"/channels/{cid}/omok/join", headers=headers)

    cards = _game_cards(await _messages(client, headers, cid))
    assert sorted(c["content"] for c in cards) == ["bingo", "omok"]


async def test_balance_game_posts_a_card(client, register):
    """밸런스게임은 join이 없고 start로 열린다."""
    token = await register(client, "a@example.com", "pass1234", "A")
    headers = {"Authorization": f"Bearer {token}"}
    server = (await client.post("/servers", json={"name": "모임"}, headers=headers)).json()
    cid = (await client.get(f"/servers/{server['id']}/channels", headers=headers)).json()[0]["id"]

    res = await client.post(
        f"/channels/{cid}/balance/start",
        json={"option_a": "탕수육 부먹", "option_b": "찍먹"},
        headers=headers,
    )
    assert res.status_code == 200

    cards = _game_cards(await _messages(client, headers, cid))
    assert len(cards) == 1
    assert cards[0]["content"] == "balance"


async def test_game_card_does_not_break_normal_chat(client, register):
    """카드가 섞여도 일반 메시지 흐름은 그대로여야 한다."""
    token = await register(client, "a@example.com", "pass1234", "A")
    headers = {"Authorization": f"Bearer {token}"}
    server = (await client.post("/servers", json={"name": "모임"}, headers=headers)).json()
    cid = (await client.get(f"/servers/{server['id']}/channels", headers=headers)).json()[0]["id"]

    await client.post(f"/channels/{cid}/messages", json={"content": "안녕"}, headers=headers)
    await client.post(f"/channels/{cid}/bingo/join", headers=headers)
    await client.post(f"/channels/{cid}/messages", json={"content": "빙고하자"}, headers=headers)

    messages = await _messages(client, headers, cid)
    kinds = [m["kind"] for m in messages]
    assert kinds.count("user") == 2
    assert kinds.count("game") == 1
    # 시간순이 유지되는지 — 카드가 중간에 끼어야 한다
    assert [m["content"] for m in messages if m["kind"] in ("user", "game")] == [
        "안녕",
        "bingo",
        "빙고하자",
    ]
