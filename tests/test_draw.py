"""공유 그림판(Whiteboard) API 테스트.

특히 좌표계(space) 필드의 하위호환을 지킨다: Redis에 이미 쌓여 있던 예전 획에는
space 키가 없으므로 "norm"(0..1 정규화)으로 읽혀야 하고, 새 클라이언트가 보내는
"px"(캔버스 픽셀) 획은 그대로 보존돼야 한다.
"""

from httpx import AsyncClient


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_channel(client: AsyncClient, token: str) -> int:
    server = (
        await client.post("/servers", json={"name": "Team"}, headers=_headers(token))
    ).json()
    channels = (
        await client.get(f"/servers/{server['id']}/channels", headers=_headers(token))
    ).json()
    return channels[0]["id"]


async def test_stroke_keeps_px_space(client: AsyncClient, register):
    token = await register(client, "a@test.com", "pass1234", "Alice")
    channel_id = await _make_channel(client, token)

    resp = await client.post(
        f"/channels/{channel_id}/draw/stroke",
        json={
            "points": [[12.5, 40.0], [130.0, 88.5]],
            "color": "#ff8b6a",
            "width": 4,
            "space": "px",
        },
        headers=_headers(token),
    )
    assert resp.status_code == 200
    assert resp.json()["space"] == "px"

    state = (
        await client.get(f"/channels/{channel_id}/draw", headers=_headers(token))
    ).json()
    stroke = state["strokes"][0]
    assert stroke["space"] == "px"
    # 픽셀 좌표는 1을 넘어도 그대로 보존돼야 한다 (정규화 강제 없음)
    assert stroke["points"] == [[12.5, 40.0], [130.0, 88.5]]


async def test_stroke_without_space_defaults_to_norm(client: AsyncClient, register):
    """space를 안 보내는 구버전 클라이언트/기존 데이터는 정규화 좌표로 읽힌다."""
    token = await register(client, "a@test.com", "pass1234", "Alice")
    channel_id = await _make_channel(client, token)

    resp = await client.post(
        f"/channels/{channel_id}/draw/stroke",
        json={"points": [[0.1, 0.2]], "color": "#2a2a33", "width": 2},
        headers=_headers(token),
    )
    assert resp.status_code == 200
    assert resp.json()["space"] == "norm"


async def test_invalid_space_rejected(client: AsyncClient, register):
    token = await register(client, "a@test.com", "pass1234", "Alice")
    channel_id = await _make_channel(client, token)

    resp = await client.post(
        f"/channels/{channel_id}/draw/stroke",
        json={"points": [[1, 2]], "color": "#2a2a33", "width": 2, "space": "meters"},
        headers=_headers(token),
    )
    assert resp.status_code == 422
