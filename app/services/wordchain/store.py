"""끝말잇기(폭탄 돌리기) 게임 세션의 상태를 Redis에 저장하고 전이시키는 저장소.

폭탄 돌리기 규칙: 게임 시작 시 하나의 '도화선(fuse) 마감 시각'을 now+120초로 고정하고
턴이 넘어가도 절대 리셋하지 않는다. 앞 단어의 끝글자로 잇는 단어를 제출하면 폭탄을 다음
사람에게 넘길 뿐, 남은 시간은 계속 흐른다. 마감이 지난 순간 폭탄을 든 사람 한 명이
패배하고 게임이 끝난다(단일 패자). 단어 규칙(한글·끝글자 잇기·두음법칙) 검증은 logic.py.

상태: 대기(waiting) → 진행(playing) → 종료(finished). 저장 방식은 게임 하나를 JSON으로
직렬화해 "game:wordchain:{채널id}" 키에 TTL과 함께 저장하고, 동시 요청은 채널별 Redis
분산 락으로 직렬화한다. 타이머는 따로 두지 않고, 요청이 들어올 때마다 도화선 경과를 지연
판정한다(초성퀴즈 store와 동일한 폭탄 엔진).
"""

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from fastapi import HTTPException, status

from app.core.redis import get_redis
from app.services.game_ttl import ttl_for
from app.services.wordchain.logic import allowed_first_chars, is_hangul_word

TTL_SECONDS = 3600
FUSE_SECONDS = 120  # 판 전체에 딱 하나 걸리는 도화선(2분). 턴마다 리셋하지 않는다.

WAITING = "waiting"
PLAYING = "playing"
FINISHED = "finished"


@dataclass
class WordChainPlayer:
    user_id: int
    display_name: str
    alive: bool = True


@dataclass
class WordEntry:
    user_id: int
    display_name: str
    word: str


@dataclass
class WordChainGame:
    channel_id: int
    status: str = WAITING
    # 몇 번째 판인지. 끝난 판에 다시 들어와 새 대기실이 열릴 때 1씩 올라간다.
    round: int = 1
    players: list[WordChainPlayer] = field(default_factory=list)
    turn_pos: int = 0
    words: list[WordEntry] = field(default_factory=list)
    used: set[str] = field(default_factory=set)
    loser_user_id: int | None = None
    last_event: str | None = None
    # 판 전체에 하나 걸린 도화선 마감 시각. 시작 시 정해지고 이후 바뀌지 않는다.
    fuse_deadline: float | None = None

    def find_player(self, user_id: int) -> WordChainPlayer | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    def current_player(self) -> WordChainPlayer | None:
        if self.status != PLAYING or not self.players:
            return None
        return self.players[self.turn_pos]


def _to_json(game: WordChainGame) -> str:
    data = asdict(game)
    data["used"] = sorted(game.used)  # set은 JSON에 없으므로 리스트로 변환
    return json.dumps(data)


def _from_json(raw: str) -> WordChainGame:
    data = json.loads(raw)
    data["players"] = [WordChainPlayer(**p) for p in data["players"]]
    data["words"] = [WordEntry(**w) for w in data["words"]]
    data["used"] = set(data["used"])
    return WordChainGame(**data)


class WordChainStore:
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
        return f"game:wordchain:{channel_id}"

    def _lock(self, channel_id: int):
        # 같은 채널에 대한 동시 요청을 워커에 상관없이 한 줄로 세우는 분산 락.
        return get_redis().lock(f"lock:wordchain:{channel_id}", timeout=10)

    async def _load(self, channel_id: int) -> WordChainGame | None:
        raw = await get_redis().get(self._key(channel_id))
        return _from_json(raw) if raw else None

    async def _save(self, game: WordChainGame) -> None:
        # 저장할 때마다 TTL을 다시 걸어준다 — 활동이 있는 게임은 계속 살아 있고,
        # 방치된 게임은 TTL 만료로 Redis에서 자동 소멸한다.
        await get_redis().set(
            self._key(game.channel_id),
            _to_json(game),
            # 대기·종료 상태로 방치되면 30초 뒤 자동으로 사라진다(game_ttl 참고).
            ex=ttl_for(game.status, self._ttl),
        )

    def _advance_turn(self, game: WordChainGame) -> None:
        # 폭탄을 다음 자리로 넘긴다(단순 라운드 로빈 — 중간 탈락 누적이 없다).
        game.turn_pos = (game.turn_pos + 1) % len(game.players)

    def _apply_timeout(self, game: WordChainGame, now: float) -> bool:
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

    def seconds_left(self, game: WordChainGame) -> int | None:
        if game.status != PLAYING or game.fuse_deadline is None:
            return None
        return max(0, int(game.fuse_deadline - self._clock()))

    async def join(self, channel_id: int, user_id: int, display_name: str) -> WordChainGame:
        async with self._lock(channel_id):
            now = self._clock()
            game = await self._load(channel_id)
            if game is None:
                game = WordChainGame(channel_id=channel_id)
            self._apply_timeout(game, now)
            if game.status == FINISHED:
                # 끝난 판에 다시 들어오면 새 대기실을 연다 (다음 라운드).
                game.status = WAITING
                game.round += 1
                game.players = []
                game.words = []
                game.used = set()
                game.turn_pos = 0
                game.loser_user_id = None
                game.last_event = None
                game.fuse_deadline = None
            if game.status == PLAYING and game.find_player(user_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="게임이 이미 진행 중이에요. 다음 라운드에 참여하세요",
                )
            if game.find_player(user_id) is None:
                game.players.append(WordChainPlayer(user_id=user_id, display_name=display_name))
            await self._save(game)
            return game

    async def start(self, channel_id: int, user_id: int) -> WordChainGame:
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
            game.last_event = f"💣 폭탄 점화! {first.display_name}님부터 아무 단어나 시작하세요"
            await self._save(game)
            return game

    async def submit(self, channel_id: int, user_id: int, word: str) -> WordChainGame:
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

            # 단어 검증 3단계: 형식(한글 2~10자) → 중복 여부 → 앞 단어 끝글자 잇기.
            word = word.strip()
            if not is_hangul_word(word):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="2~10글자 한글 단어를 입력하세요",
                )
            if word in game.used:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="이미 나온 단어예요",
                )
            if game.words:
                # 두음법칙 변형(예: 례→예)까지 포함해 허용 글자를 구해 비교한다.
                allowed = allowed_first_chars(game.words[-1].word)
                if word[0] not in allowed:
                    pretty = "/".join(sorted(allowed))
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail=f"'{pretty}'(으)로 시작하는 단어여야 해요",
                    )

            # 정답이면 폭탄을 다음 사람에게 넘긴다. 도화선은 그대로 흐른다.
            game.words.append(
                WordEntry(user_id=user_id, display_name=current.display_name, word=word)
            )
            game.used.add(word)
            self._advance_turn(game)
            nxt = game.current_player()
            game.last_event = (
                f"'{word}' → 폭탄이 {nxt.display_name if nxt else '?'}님에게 넘어갔어요"
            )
            await self._save(game)
            return game

    async def get(self, channel_id: int) -> tuple[WordChainGame | None, bool]:
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


store = WordChainStore()


def get_wordchain_store() -> WordChainStore:
    return store
