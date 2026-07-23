"""초성퀴즈(폭탄 돌리기) 게임 세션의 상태를 Redis에 저장하고 전이시키는 저장소.

routers/chosung.py의 각 엔드포인트가 호출하는 진입점이며, 단어 자체의 유효성
검사(한글·초성 일치)는 logic.py에 위임한다. 상태는 대기(waiting) → 진행(playing) →
종료(finished) 순서로 전이한다.

폭탄 돌리기 규칙: 게임 시작 시 하나의 '도화선(fuse) 마감 시각'을 now+120초로 고정하고
턴이 넘어가도 절대 리셋하지 않는다. 초성 문제를 맞히면 폭탄을 다음 사람에게 넘기고 새
문제를 낼 뿐, 남은 시간은 그대로 흐른다. 마감이 지난 순간 폭탄을 든 사람 한 명이 패배하고
게임이 끝난다(단일 패자).

저장 방식은 끝말잇기(services/wordchain/store.py)와 동일하다 — 게임 하나를 JSON으로
직렬화해 "game:chosung:{채널id}" 키에 TTL과 함께 저장하고, 방치된 게임은 Redis TTL
만료로 자동 소멸한다. 동시 요청은 채널별 Redis 분산 락으로 직렬화한다.
"""

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from fastapi import HTTPException, status

from app.core.redis import get_redis
from app.services.chosung.logic import initials, is_hangul_word, random_prompt
from app.services.game_ttl import ttl_for

TTL_SECONDS = 3600
FUSE_SECONDS = 120  # 판 전체에 딱 하나 걸리는 도화선(2분). 턴마다 리셋하지 않는다.

WAITING = "waiting"
PLAYING = "playing"
FINISHED = "finished"


@dataclass
class ChosungPlayer:
    user_id: int
    display_name: str
    alive: bool = True


@dataclass
class ChosungGame:
    channel_id: int
    status: str = WAITING
    # 몇 번째 판인지. 끝난 판에 다시 들어와 새 대기실이 열릴 때 1씩 올라간다.
    round: int = 1
    players: list[ChosungPlayer] = field(default_factory=list)
    turn_pos: int = 0
    # 현재 폭탄 든 사람이 풀어야 할 초성 문제(예: 'ㅅㄱ').
    prompt: str | None = None
    # 지금까지 제출된 정답 단어들(표시용 히스토리).
    words: list[str] = field(default_factory=list)
    used: set[str] = field(default_factory=set)
    loser_user_id: int | None = None
    last_event: str | None = None
    # 판 전체에 하나 걸린 도화선 마감 시각. 시작 시 정해지고 이후 바뀌지 않는다.
    fuse_deadline: float | None = None

    def find_player(self, user_id: int) -> ChosungPlayer | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    def current_player(self) -> ChosungPlayer | None:
        if self.status != PLAYING or not self.players:
            return None
        return self.players[self.turn_pos]


def _to_json(game: ChosungGame) -> str:
    data = asdict(game)
    data["used"] = sorted(game.used)  # set은 JSON에 없으므로 리스트로 변환
    return json.dumps(data)


def _from_json(raw: str) -> ChosungGame:
    data = json.loads(raw)
    data["players"] = [ChosungPlayer(**p) for p in data["players"]]
    data["used"] = set(data["used"])
    return ChosungGame(**data)


class ChosungStore:
    def __init__(
        self,
        ttl_seconds: float = TTL_SECONDS,
        fuse_seconds: float = FUSE_SECONDS,
        # 도화선 마감 시각이 Redis를 거쳐 워커 간에 공유되므로, 프로세스마다 기준이
        # 다른 monotonic 대신 벽시계(time.time)를 쓴다.
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._ttl = ttl_seconds
        self._fuse = fuse_seconds
        self._clock = clock

    def _key(self, channel_id: int) -> str:
        return f"game:chosung:{channel_id}"

    def _lock(self, channel_id: int):
        # 같은 채널에 대한 동시 요청을 워커에 상관없이 한 줄로 세우는 분산 락.
        return get_redis().lock(f"lock:chosung:{channel_id}", timeout=10)

    async def _load(self, channel_id: int) -> ChosungGame | None:
        raw = await get_redis().get(self._key(channel_id))
        return _from_json(raw) if raw else None

    async def _save(self, game: ChosungGame) -> None:
        # 저장할 때마다 TTL을 다시 걸어준다 — 활동이 있는 게임은 계속 살아 있고,
        # 방치된 게임은 TTL 만료로 Redis에서 자동 소멸한다.
        await get_redis().set(
            self._key(game.channel_id),
            _to_json(game),
            # 대기·종료 상태로 방치되면 30초 뒤 자동으로 사라진다(game_ttl 참고).
            ex=ttl_for(game.status, self._ttl),
        )

    def _advance_turn(self, game: ChosungGame) -> None:
        # 폭탄을 다음 자리로 넘긴다(단순 라운드 로빈 — 초성퀴즈는 탈락 누적이 없다).
        game.turn_pos = (game.turn_pos + 1) % len(game.players)

    def _apply_timeout(self, game: ChosungGame, now: float) -> bool:
        """도화선이 다 탔으면 폭탄을 든 사람을 패자로 확정한다. 바뀌었으면 True.

        타이머를 따로 두지 않고, 요청이 들어올 때마다(join/start/submit/get) 이 함수를
        호출해 마감이 지났는지 확인한다. 패자는 단 한 명이고 그 즉시 게임이 끝난다.
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

    def seconds_left(self, game: ChosungGame) -> int | None:
        if game.status != PLAYING or game.fuse_deadline is None:
            return None
        return max(0, int(game.fuse_deadline - self._clock()))

    async def join(self, channel_id: int, user_id: int, display_name: str) -> ChosungGame:
        async with self._lock(channel_id):
            now = self._clock()
            game = await self._load(channel_id)
            if game is None:
                game = ChosungGame(channel_id=channel_id)
            self._apply_timeout(game, now)
            if game.status == FINISHED:
                # 끝난 판에 다시 들어오면 새 대기실을 연다 (다음 라운드).
                game.status = WAITING
                game.round += 1
                game.players = []
                game.words = []
                game.used = set()
                game.turn_pos = 0
                game.prompt = None
                game.loser_user_id = None
                game.last_event = None
                game.fuse_deadline = None
            if game.status == PLAYING and game.find_player(user_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="게임이 이미 진행 중이에요. 다음 라운드에 참여하세요",
                )
            if game.find_player(user_id) is None:
                game.players.append(ChosungPlayer(user_id=user_id, display_name=display_name))
            await self._save(game)
            return game

    async def start(self, channel_id: int, user_id: int) -> ChosungGame:
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
            game.prompt = random_prompt()
            first = game.players[0]
            game.last_event = f"💣 폭탄 점화! {first.display_name}님부터 초성을 맞혀 넘기세요"
            await self._save(game)
            return game

    async def submit(self, channel_id: int, user_id: int, word: str) -> ChosungGame:
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

            # 단어 검증: 형식(한글 2~3자) → 길이 일치 → 초성 글자별 일치 → 중복 여부.
            # 정답에 대한 사전 검사는 하지 않는다(초성이 프롬프트와 맞으면 통과).
            word = word.strip()
            prompt = game.prompt or ""
            if not is_hangul_word(word):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="한글 단어를 입력하세요",
                )
            if len(word) != len(prompt) or initials(word) != prompt:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"초성 '{prompt}'에 맞는 단어여야 해요",
                )
            if word in game.used:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="이미 나온 단어예요",
                )

            # 정답이면 폭탄을 다음 사람에게 넘기고 새 문제를 낸다. 도화선은 그대로.
            game.words.append(word)
            game.used.add(word)
            self._advance_turn(game)
            game.prompt = random_prompt()
            nxt = game.current_player()
            game.last_event = (
                f"{current.display_name}님이 '{word}' 정답! 폭탄이 "
                f"{nxt.display_name if nxt else '?'}님에게 넘어갔어요"
            )
            await self._save(game)
            return game

    async def get(self, channel_id: int) -> tuple[ChosungGame | None, bool]:
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


store = ChosungStore()


def get_chosung_store() -> ChosungStore:
    return store
