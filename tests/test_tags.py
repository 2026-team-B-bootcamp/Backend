import math

from httpx import AsyncClient

import app.services.ai.embedding as ai_embedding
from app.core.config import settings


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _vec(x: float, y: float) -> list[float]:
    """2차원 좌표를 tag_embedding_dim 차원으로 패딩한 단위 벡터를 만든다."""
    norm = math.sqrt(x * x + y * y)
    return [x / norm, y / norm] + [0.0] * (settings.tag_embedding_dim - 2)


class FakeEmbeddingProvider(ai_embedding.EmbeddingProvider):
    """텍스트별로 미리 정한 벡터를 돌려주는 가짜 프로바이더 (호출 기록 포함)."""

    def __init__(self, table: dict[str, list[float]]) -> None:
        self.table = table
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        self.calls.append(list(texts))
        return [self.table[t] for t in texts]


# 코사인 유사도: 포켓몬↔피카츄 ≈ 0.95(유사), 포켓몬↔요리 = 0(무관)
_EMBEDDINGS = {
    "포켓몬": _vec(1.0, 0.0),
    "피카츄": _vec(0.95, math.sqrt(1 - 0.95**2)),
    "요리": _vec(0.0, 1.0),
    "영화": _vec(-1.0, 0.0),
    "커피": _vec(0.0, -1.0),
    "게임": _vec(0.5, 0.5),
}


def _install_fake_provider(monkeypatch) -> FakeEmbeddingProvider:
    fake = FakeEmbeddingProvider(_EMBEDDINGS)
    monkeypatch.setattr(ai_embedding, "_provider", fake)
    return fake


async def _create_server(client: AsyncClient, token: str) -> dict:
    return (
        await client.post("/servers", json={"name": "Team"}, headers=_headers(token))
    ).json()


async def test_upsert_reflected_in_members(client: AsyncClient, register):
    token = await register(client, "a@test.com", "pass1234", "Alice")
    server = await _create_server(client, token)

    resp = await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "축구", "tag2": "영화", "tag3": "커피"},
        headers=_headers(token),
    )
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["축구", "영화", "커피"]

    members = (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token))
    ).json()
    me = members[0]
    assert me["tags"] == ["축구", "영화", "커피"]


async def test_upsert_overwrites(client: AsyncClient, register):
    token = await register(client, "a@test.com", "pass1234", "Alice")
    server = await _create_server(client, token)
    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "축구", "tag2": "영화", "tag3": "커피"},
        headers=_headers(token),
    )
    resp = await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "농구", "tag2": "게임", "tag3": "독서"},
        headers=_headers(token),
    )
    assert resp.json()["tags"] == ["농구", "게임", "독서"]


async def test_common_tags_intersection(client: AsyncClient, register):
    token_a = await register(client, "a@test.com", "pass1234", "Alice")
    token_b = await register(client, "b@test.com", "pass1234", "Bob")
    server = await _create_server(client, token_a)
    await client.post(
        "/servers/join",
        json={"invite_code": server["invite_code"]},
        headers=_headers(token_b),
    )

    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "축구", "tag2": "영화", "tag3": "커피"},
        headers=_headers(token_a),
    )
    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "축구", "tag2": "게임", "tag3": "독서"},
        headers=_headers(token_b),
    )

    members = (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token_a))
    ).json()
    bob = next(m for m in members if m["display_name"] == "Bob")
    assert bob["common_with_me"] == ["축구"]
    alice = next(m for m in members if m["display_name"] == "Alice")
    assert alice["common_with_me"] == []


async def test_similar_tags_count_as_common(client: AsyncClient, register, monkeypatch):
    """완전일치가 없어도 임베딩 유사도가 임계값 이상이면 겹치는 관심사로 표시된다."""
    fake = _install_fake_provider(monkeypatch)
    monkeypatch.setattr(settings, "tag_similarity_threshold", 0.8)

    token_a = await register(client, "a@test.com", "pass1234", "Alice")
    token_b = await register(client, "b@test.com", "pass1234", "Bob")
    server = await _create_server(client, token_a)
    await client.post(
        "/servers/join",
        json={"invite_code": server["invite_code"]},
        headers=_headers(token_b),
    )

    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "포켓몬", "tag2": "영화", "tag3": "커피"},
        headers=_headers(token_a),
    )
    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "피카츄", "tag2": "요리", "tag3": "게임"},
        headers=_headers(token_b),
    )

    members = (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token_a))
    ).json()
    bob = next(m for m in members if m["display_name"] == "Bob")
    # 포켓몬↔피카츄(유사도 0.95)만 매칭. 요리(0)·게임(0.707)은 임계값 미달.
    assert bob["common_with_me"] == ["피카츄"]

    # 반대 방향(Bob 시점)에선 Alice의 "포켓몬"이 매칭된다.
    members = (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token_b))
    ).json()
    alice = next(m for m in members if m["display_name"] == "Alice")
    assert alice["common_with_me"] == ["포켓몬"]
    assert fake.calls  # 실제로 가짜 프로바이더가 쓰였는지 확인


async def test_embeddings_cached_per_tag_text(client: AsyncClient, register, monkeypatch):
    """이미 임베딩된 태그 텍스트는 (다른 유저가 등록해도) 다시 임베딩하지 않는다."""
    fake = _install_fake_provider(monkeypatch)

    token_a = await register(client, "a@test.com", "pass1234", "Alice")
    token_b = await register(client, "b@test.com", "pass1234", "Bob")
    server = await _create_server(client, token_a)
    await client.post(
        "/servers/join",
        json={"invite_code": server["invite_code"]},
        headers=_headers(token_b),
    )

    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "포켓몬", "tag2": "영화", "tag3": "커피"},
        headers=_headers(token_a),
    )
    assert fake.calls == [["영화", "커피", "포켓몬"]]  # 정렬된 배치 1회

    # Bob의 태그 중 "포켓몬"은 이미 있으므로 새 텍스트 2개만 임베딩된다.
    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "피카츄", "tag2": "포켓몬", "tag3": "요리"},
        headers=_headers(token_b),
    )
    assert fake.calls[1] == ["요리", "피카츄"]

    # 같은 태그를 다시 저장하면 임베딩 호출이 아예 없다.
    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "피카츄", "tag2": "포켓몬", "tag3": "요리"},
        headers=_headers(token_b),
    )
    assert len(fake.calls) == 2


async def test_without_embeddings_exact_match_only(client: AsyncClient, register):
    """임베딩 프로바이더가 없으면(키 없음/실패) 완전일치만 동작하고 에러가 없다."""
    # conftest의 autouse null_embedding 픽스처가 NullEmbeddingProvider를 꽂아둔 상태.
    token_a = await register(client, "a@test.com", "pass1234", "Alice")
    token_b = await register(client, "b@test.com", "pass1234", "Bob")
    server = await _create_server(client, token_a)
    await client.post(
        "/servers/join",
        json={"invite_code": server["invite_code"]},
        headers=_headers(token_b),
    )

    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "포켓몬", "tag2": "영화", "tag3": "커피"},
        headers=_headers(token_a),
    )
    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "피카츄", "tag2": "영화", "tag3": "요리"},
        headers=_headers(token_b),
    )

    members = (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token_a))
    ).json()
    bob = next(m for m in members if m["display_name"] == "Bob")
    # 완전일치인 "영화"만 매칭 — 포켓몬↔피카츄는 임베딩이 없어 매칭 불가.
    assert bob["common_with_me"] == ["영화"]


async def test_non_member_cannot_set_tags_403(client: AsyncClient, register):
    token_a = await register(client, "a@test.com", "pass1234", "Alice")
    token_b = await register(client, "b@test.com", "pass1234", "Bob")
    server = await _create_server(client, token_a)
    resp = await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "x", "tag2": "y", "tag3": "z"},
        headers=_headers(token_b),
    )
    assert resp.status_code == 403
