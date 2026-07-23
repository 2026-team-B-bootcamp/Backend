"""링크 미리보기(OpenGraph 언퍼) 서비스.

요청 흐름: 라우터 → fetch_preview(url) → Redis 캐시 확인 → (미스면) 서버에서 URL을
직접 받아와 <head>의 OG/twitter 메타·<title>을 뽑아 미리보기 dict를 만들고 캐시에 저장.

보안이 핵심이다. 이 엔드포인트는 "서버가 임의 URL을 대신 요청"하는 구조라
사내망·클라우드 메타데이터(169.254.169.254)·localhost를 노린 SSRF의 표적이 된다.
그래서 요청 전에 hostname을 실제로 DNS 해석해 사설/루프백/링크로컬/예약 IP면 거부하고,
리다이렉트도 매 홉마다 같은 검사를 다시 한다(리다이렉트로 내부망을 가리키는 우회 차단).

캐시: 성공 결과는 하루(86400s), "미리보기 없음"도 1시간 동안 negative 캐시로 둔다.
같은 링크가 채팅에 여러 번 뜰 때마다 원서버를 두드리는 refetch 폭주를 막기 위함이다.
"""

import asyncio
import ipaddress
import json
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from app.core.redis import get_redis

_CACHE_PREFIX = "link:preview:"
_TTL_OK = 86400  # 성공 결과 1일
_TTL_NEGATIVE = 3600  # "미리보기 없음" 1시간
# negative 캐시 마커. 이 값이 저장돼 있으면 "이미 시도했지만 미리보기가 없더라"는 뜻.
_NEGATIVE = "\x00none"

_TIMEOUT = 5.0
_MAX_BYTES = 512 * 1024  # 512KB — HTML <head>만 필요하므로 넉넉하고도 짧게 자른다.
_MAX_REDIRECTS = 5
# 브라우저처럼 보이게: 일부 사이트는 UA 없는 요청에 OG 태그를 안 준다.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LinkPreviewBot/1.0)",
    "Accept": "text/html,application/xhtml+xml",
}


class _OGParser(HTMLParser):
    """<head>에서 미리보기에 쓸 메타 태그와 <title>만 골라 모은다.

    html.parser는 표준 라이브러리라 새 의존성(bs4 등)이 필요 없다.
    og: 우선, 없으면 twitter:/일반 name 순으로 채운다.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title: str | None = None
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
            return
        if tag != "meta":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        # property(OG 표준) 또는 name(twitter/일반) 어느 쪽이든 키로 인정.
        key = (a.get("property") or a.get("name") or "").lower()
        content = a.get("content")
        if key and content and key not in self.meta:
            self.meta[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)

    def close(self) -> None:  # noqa: D102
        super().close()
        if self._title_parts:
            self.title = "".join(self._title_parts).strip() or None


def _is_public_ip(ip: str) -> bool:
    """SSRF 차단 판정: 사설/루프백/링크로컬/예약/멀티캐스트 대역이면 False."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


async def _host_is_public(host: str) -> bool:
    """hostname을 실제 DNS 해석해 나온 모든 IP가 공인 대역인지 확인한다.

    하나라도 내부 대역이면 거부한다(예: `foo.example.com`이 127.0.0.1로 해석되는 경우).

    DNS 해석은 스레드 풀(loop.getaddrinfo)로 넘긴다 — 동기 socket.getaddrinfo를
    이벤트 루프에서 직접 부르면 느린/무응답 DNS 링크 하나가 워커 전체를 멈춘다.

    ⚠ 잔존 위험(클라우드 배포 전 보강 필요): 여기서 검증한 IP와 httpx가 실제
    연결 시 다시 해석하는 IP가 다를 수 있다(저 TTL DNS 리바인딩 TOCTOU). 완전
    차단하려면 검증한 IP를 그대로 고정해 연결해야 한다(커스텀 트랜스포트/이그레스
    프록시). 로컬/데모에서는 위험이 낮아 현 검사로 충분하다.
    """
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    if not infos:
        return False
    return all(_is_public_ip(info[4][0]) for info in infos)


async def _valid_target(url: str) -> bool:
    """스킴이 http/https이고 호스트가 공인 IP로 해석되는지 검사(요청 직전 게이트)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return await _host_is_public(parsed.hostname)


async def _fetch_html(url: str) -> tuple[str, str] | None:
    """URL을 받아와 (최종 URL, HTML 텍스트)를 돌려준다. 미리보기 불가면 None.

    리다이렉트는 직접 따라가며(follow_redirects=False) 매 홉마다 SSRF 검사를 다시 한다.
    text/html이 아니거나 512KB를 넘어서면 조기 중단한다.
    """
    async with httpx.AsyncClient(
        timeout=_TIMEOUT, follow_redirects=False, headers=_HEADERS
    ) as client:
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            if not await _valid_target(current):
                return None
            try:
                async with client.stream("GET", current) as resp:
                    # 3xx면 Location으로 이동해 다음 홉을 다시 검사한다.
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            return None
                        current = urljoin(current, location)
                        continue
                    if resp.status_code != 200:
                        return None
                    ctype = resp.headers.get("content-type", "")
                    if "text/html" not in ctype.lower():
                        return None
                    # content-length가 있고 상한을 넘으면 본문을 읽지도 않는다.
                    clen = resp.headers.get("content-length")
                    if clen and clen.isdigit() and int(clen) > _MAX_BYTES:
                        return None
                    # 스트리밍으로 조금씩 읽되 상한을 넘으면 그 지점에서 자른다.
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= _MAX_BYTES:
                            break
                    raw = b"".join(chunks)[:_MAX_BYTES]
                    encoding = resp.encoding or "utf-8"
                    try:
                        text = raw.decode(encoding, errors="replace")
                    except (LookupError, TypeError):
                        text = raw.decode("utf-8", errors="replace")
                    return str(resp.url), text
            except (httpx.HTTPError, ValueError):
                return None
        # 리다이렉트가 너무 깊으면 포기.
        return None


def _extract(final_url: str, html: str) -> dict[str, str | None]:
    """파싱한 메타에서 제목/설명/이미지/사이트명을 og: → twitter/일반 순으로 고른다."""
    parser = _OGParser()
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, ValueError):
        # 깨진 HTML이라도 그때까지 모은 메타/타이틀은 그대로 쓴다.
        pass
    meta = parser.meta

    title = meta.get("og:title") or meta.get("twitter:title") or parser.title
    description = (
        meta.get("og:description")
        or meta.get("twitter:description")
        or meta.get("description")
    )
    image = meta.get("og:image") or meta.get("og:image:url") or meta.get("twitter:image")
    site_name = meta.get("og:site_name")

    # 상대경로 이미지(/og.png)는 최종 URL 기준 절대경로로 바꾼다.
    if image:
        image = urljoin(final_url, image.strip())

    def _clean(value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    return {
        "title": _clean(title),
        "description": _clean(description),
        "image": _clean(image),
        "site_name": _clean(site_name),
    }


async def fetch_preview(url: str) -> dict | None:
    """링크 미리보기 dict를 돌려준다. 미리보기를 만들 수 없으면 None.

    반환 dict 형태: {url, title, description, image, site_name} (각 값은 None 가능).
    Redis 캐시를 먼저 확인하고, 미스면 원서버에서 받아와 결과(성공/실패 모두)를 캐시한다.
    """
    r = get_redis()
    cache_key = f"{_CACHE_PREFIX}{url}"

    cached = await r.get(cache_key)
    if cached is not None:
        if cached == _NEGATIVE:
            return None
        try:
            return json.loads(cached)
        except (ValueError, TypeError):
            pass  # 손상된 캐시는 무시하고 다시 받아온다.

    fetched = await _fetch_html(url)
    if fetched is None:
        await r.set(cache_key, _NEGATIVE, ex=_TTL_NEGATIVE)
        return None

    final_url, html = fetched
    data = _extract(final_url, html)
    # 쓸 만한 정보가 하나도 없으면 미리보기로 치지 않는다(negative 캐시).
    if not any(data.values()):
        await r.set(cache_key, _NEGATIVE, ex=_TTL_NEGATIVE)
        return None

    result = {"url": url, **data}
    await r.set(cache_key, json.dumps(result), ex=_TTL_OK)
    return result
