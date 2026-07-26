"""폭탄 돌리기(도화선) 게임의 공통 엔진 — 끝말잇기(wordchain)와 초성퀴즈(chosung)가 쓴다.

두 게임은 "제출 규칙"(끝글자 잇기 vs 초성 일치)과 "문제/기록 필드"(없음 vs `prompt`,
`WordEntry` vs `str`)만 다르고, 그 외 전부 — 도화선 타이머, 턴 로테이션, 라운드 전환,
Redis 저장/락 — 는 글자 그대로 같다. 그래서 그 공통 부분만 이 모듈의 `BombGameStore`가
갖고, 게임마다 다른 부분은 서브클래스가 훅 메서드로 채운다.

폭탄 돌리기 규칙: 게임 시작 시 하나의 '도화선(fuse) 마감 시각'을 now+120초로 고정하고
턴이 넘어가도 절대 리셋하지 않는다. 정답을 제출하면 폭탄을 다음 사람에게 넘길 뿐, 남은
시간은 계속 흐른다. 마감이 지난 순간 폭탄을 든 사람 한 명이 패배하고 게임이 끝난다
(단일 패자). 타이머를 따로 두지 않고, 요청이 들어올 때마다(join/start/submit/get)
도화선 경과를 지연 판정한다.

상태: 대기(waiting) → 진행(playing) → 종료(finished). 저장 방식은 게임 하나를 JSON으로
직렬화해 "game:{종류}:{채널id}" 키에 TTL과 함께 저장하고, 방치된 게임은 Redis TTL 만료로
자동 소멸한다. 동시 요청은 채널별 Redis 분산 락으로 직렬화한다.

## 서브클래스가 구현해야 하는 훅
- `_new_game(channel_id)`: 빈 게임 생성.
- `_reset_for_new_round(game)`: 끝난 판에 다시 들어왔을 때(=다음 라운드) 게임 고유
  필드(단어 목록·프롬프트 등)를 초기화한다. 공통 필드는 베이스의 `join()`이 처리한다.
- `_apply_start(game, first)`: 도화선 점화 시 게임 고유 초기값(예: 첫 초성 문제)을
  설정하고, 시작 안내 `last_event` 문구를 돌려준다.
- `_validate_and_apply(game, word, current)`: 제출된 단어를 검증한다. 실패하면
  `HTTPException(422)`을 그대로 던진다(이 검증 **순서**가 API 응답의 `detail` 문구를
  결정하므로 게임마다 원래 순서를 그대로 유지해야 한다). 성공하면 `game.words`에 정답을
  반영하고, `game.used.add(word)` → `self._advance_turn(game)` → (필요하면 다음 문제
  갱신) 까지 마친 뒤 안내 `last_event` 문구를 반환한다.
- `_from_json(raw)`: 저장된 JSON을 게임 dataclass로 복원한다. 공통 필드(players/used/
  host_user_id)는 `self._prepare_common()`으로 되돌리고, 게임 고유 필드만 추가로 변환한다.
"""

import json
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from fastapi import HTTPException, status

from app.core.redis import get_redis
from app.services.game_ttl import ttl_for

TTL_SECONDS = 3600
FUSE_SECONDS = 120  # 판 전체에 딱 하나 걸리는 도화선(2분). 턴마다 리셋하지 않는다.

WAITING = "waiting"
PLAYING = "playing"
FINISHED = "finished"


@dataclass
class BombPlayer:
    user_id: int
    display_name: str
    alive: bool = True


@dataclass
class BombGame:
    channel_id: int
    status: str = WAITING
    # 이 판을 연 사람(방장). 강제 종료 권한의 기준이며, 새 라운드가 열릴 때마다
    # 그 라운드를 다시 연 사람으로 바뀐다 (game_host.py 참고).
    host_user_id: int | None = None
    # 몇 번째 판인지. 끝난 판에 다시 들어와 새 대기실이 열릴 때 1씩 올라간다.
    round: int = 1
    players: list[BombPlayer] = field(default_factory=list)
    turn_pos: int = 0
    used: set[str] = field(default_factory=set)
    loser_user_id: int | None = None
    last_event: str | None = None
    # 판 전체에 하나 걸린 도화선 마감 시각. 시작 시 정해지고 이후 바뀌지 않는다.
    fuse_deadline: float | None = None

    def find_player(self, user_id: int) -> BombPlayer | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    def current_player(self) -> BombPlayer | None:
        if self.status != PLAYING or not self.players:
            return None
        return self.players[self.turn_pos]


class BombGameStore[GameT: BombGame](ABC):
    def __init__(
        self,
        game_kind: str,
        ttl_seconds: float = TTL_SECONDS,
        fuse_seconds: float = FUSE_SECONDS,
        # 도화선 마감 시각이 Redis를 거쳐 워커 간에 공유되므로, 프로세스마다 기준이
        # 다른 monotonic 대신 벽시계(time.time)를 쓴다.
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._kind = game_kind
        self._ttl = ttl_seconds
        self._fuse = fuse_seconds
        self._clock = clock

    # ---------------------------------------------------------------
    # 서브클래스 훅
    # ---------------------------------------------------------------

    @abstractmethod
    def _new_game(self, channel_id: int) -> GameT: ...

    @abstractmethod
    def _reset_for_new_round(self, game: GameT) -> None: ...

    @abstractmethod
    def _apply_start(self, game: GameT, first: BombPlayer) -> str: ...

    @abstractmethod
    def _validate_and_apply(self, game: GameT, word: str, current: BombPlayer) -> str: ...

    @abstractmethod
    def _from_json(self, raw: str) -> GameT: ...

    def _prepare_common(self, data: dict) -> dict:
        """역직렬화 공통부: players/used/host_user_id를 원래 타입으로 되돌린다.

        서브클래스의 `_from_json`이 게임 고유 필드(words/prompt 등)까지 마저 변환한
        뒤 이 dict를 게임 dataclass 생성자에 그대로 넘기면 된다.
        """
        data["players"] = [BombPlayer(**p) for p in data["players"]]
        data["used"] = set(data["used"])
        # 방장 도입 이전에 저장된 판에는 이 키가 없다 — 없으면 방장 없는 판으로 둔다.
        data.setdefault("host_user_id", None)
        return data

    # ---------------------------------------------------------------
    # Redis 저장/락 (게임 종류에 무관하게 완전히 동일)
    # ---------------------------------------------------------------

    def _key(self, channel_id: int) -> str:
        return f"game:{self._kind}:{channel_id}"

    def _lock(self, channel_id: int):
        # 같은 채널에 대한 동시 요청을 워커에 상관없이 한 줄로 세우는 분산 락.
        return get_redis().lock(f"lock:{self._kind}:{channel_id}", timeout=10)

    async def _load(self, channel_id: int) -> GameT | None:
        raw = await get_redis().get(self._key(channel_id))
        return self._from_json(raw) if raw else None

    def _to_json(self, game: GameT) -> str:
        data = asdict(game)
        data["used"] = sorted(game.used)  # set은 JSON에 없으므로 리스트로 변환
        return json.dumps(data)

    async def _save(self, game: GameT) -> None:
        # 저장할 때마다 TTL을 다시 걸어준다 — 활동이 있는 게임은 계속 살아 있고,
        # 방치된 게임은 TTL 만료로 Redis에서 자동 소멸한다.
        await get_redis().set(
            self._key(game.channel_id),
            self._to_json(game),
            # 대기·종료 상태로 방치되면 30초 뒤 자동으로 사라진다(game_ttl 참고).
            ex=ttl_for(game.status, self._ttl),
        )

    # ---------------------------------------------------------------
    # 도화선 엔진 (게임 종류에 무관하게 완전히 동일)
    # ---------------------------------------------------------------

    def _advance_turn(self, game: GameT) -> None:
        # 폭탄을 다음 자리로 넘긴다(단순 라운드 로빈 — 중간 탈락 누적이 없다).
        game.turn_pos = (game.turn_pos + 1) % len(game.players)

    def _apply_timeout(self, game: GameT, now: float) -> bool:
        """도화선이 다 탔으면 폭탄을 든 사람을 패자로 확정한다. 바뀌었으면 True.

        요청이 들어올 때마다(join/start/submit/get) 호출해 마감 경과를 지연 판정한다.
        패자는 단 한 명이고 그 즉시 게임이 끝난다.
        """
        if (
            game.status == PLAYING
            and game.fuse_deadline is not None
            and now >= game.fuse_deadline
        ):
            current = game.players[game.turn_pos]
            current.alive = False
            game.status = FINISHED
            game.loser_user_id = current.user_id
            game.last_event = f"💥 {current.display_name}님 손에서 폭탄이 터졌어요!"
            game.fuse_deadline = None
            return True
        return False

    def seconds_left(self, game: GameT) -> int | None:
        if game.status != PLAYING or game.fuse_deadline is None:
            return None
        return max(0, int(game.fuse_deadline - self._clock()))

    # ---------------------------------------------------------------
    # 라이프사이클
    # ---------------------------------------------------------------

    async def join(self, channel_id: int, user_id: int, display_name: str) -> GameT:
        async with self._lock(channel_id):
            now = self._clock()
            game = await self._load(channel_id)
            if game is None:
                game = self._new_game(channel_id)
            self._apply_timeout(game, now)
            if game.status == FINISHED:
                # 끝난 판에 다시 들어오면 새 대기실을 연다 (다음 라운드).
                game.status = WAITING
                game.round += 1
                game.players = []
                game.used = set()
                game.turn_pos = 0
                game.loser_user_id = None
                game.last_event = None
                game.fuse_deadline = None
                # 새 라운드를 연 사람이 그 판의 방장이 된다.
                game.host_user_id = None
                self._reset_for_new_round(game)  # 게임 고유 필드(단어 목록·프롬프트 등)
            if game.status == PLAYING and game.find_player(user_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="게임이 이미 진행 중이에요. 다음 라운드에 참여하세요",
                )
            if game.host_user_id is None:
                game.host_user_id = user_id
            if game.find_player(user_id) is None:
                game.players.append(BombPlayer(user_id=user_id, display_name=display_name))
            await self._save(game)
            return game

    async def start(self, channel_id: int, user_id: int) -> GameT:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None or game.find_player(user_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="먼저 게임에 참여하세요"
                )
            if game.status != WAITING:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="대기 중인 게임이 아니에요"
                )
            if len(game.players) < 2:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="2명 이상 모여야 시작할 수 있어요",
                )
            now = self._clock()
            game.status = PLAYING
            game.turn_pos = 0
            # 도화선은 여기서 한 번만 정해지고, 이후 턴이 넘어가도 절대 리셋하지 않는다.
            game.fuse_deadline = now + self._fuse
            first = game.players[0]
            # 게임 고유 초기값(예: 첫 초성 문제) 설정과 시작 안내 문구는 서브클래스 책임.
            game.last_event = self._apply_start(game, first)
            await self._save(game)
            return game

    async def submit(self, channel_id: int, user_id: int, word: str) -> GameT:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="진행 중인 게임이 없어요"
                )
            now = self._clock()
            if self._apply_timeout(game, now):
                # 제출하려는 바로 그 순간 도화선이 터졌다면, 409로 끊지 말고 방금
                # 확정된 FINISHED 상태를 그대로 돌려준다 — 라우터가 이 요청에서
                # _broadcast_state를 호출해 폭발을 전원에게 알린다(폴링에만 의존 X).
                await self._save(game)
                return game
            if game.status != PLAYING:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="진행 중인 게임이 아니에요"
                )
            current = game.current_player()
            if current is None or current.user_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="지금은 당신 차례가 아니에요"
                )

            word = word.strip()
            # 검증(실패 시 422, 그 순서 자체가 API 계약) → 정답 반영 → 폭탄 전달 →
            # 안내 문구까지 전부 서브클래스 책임이다(_validate_and_apply 문서 참고).
            game.last_event = self._validate_and_apply(game, word, current)
            await self._save(game)
            return game

    async def get(self, channel_id: int) -> tuple[GameT | None, bool]:
        """게임과 함께, 지연 타임아웃 판정으로 상태가 바뀌었는지를 돌려준다."""
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None:
                return None, False
            changed = self._apply_timeout(game, self._clock())
            if changed:
                await self._save(game)
            return game, changed

    async def status(self, channel_id: int) -> str:
        game = await self._load(channel_id)
        if game is None:
            return "none"
        # 지연 타임아웃을 반영해 정확한 상태를 계산한다(저장은 하지 않음 — 표시용)
        self._apply_timeout(game, self._clock())
        return game.status

    async def host(self, channel_id: int) -> int | None:
        game = await self._load(channel_id)
        return game.host_user_id if game else None

    async def clear(self, channel_id: int) -> None:
        """판을 통째로 지운다 — 방장의 강제 종료용(games 라우터가 호출)."""
        await get_redis().delete(self._key(channel_id))
