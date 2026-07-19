import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import HTTPException, status

from app.services.wordchain.logic import allowed_first_chars, is_hangul_word

TTL_SECONDS = 3600
TURN_SECONDS = 30

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
    players: list[WordChainPlayer] = field(default_factory=list)
    turn_pos: int = 0
    words: list[WordEntry] = field(default_factory=list)
    used: set[str] = field(default_factory=set)
    winner_user_id: int | None = None
    last_event: str | None = None
    turn_deadline: float | None = None
    last_touched: float = 0.0

    def find_player(self, user_id: int) -> WordChainPlayer | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    def alive_players(self) -> list[WordChainPlayer]:
        return [p for p in self.players if p.alive]

    def current_player(self) -> WordChainPlayer | None:
        if self.status != PLAYING or not self.players:
            return None
        return self.players[self.turn_pos]


class WordChainStore:
    def __init__(
        self,
        ttl_seconds: float = TTL_SECONDS,
        turn_seconds: float = TURN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._games: dict[int, WordChainGame] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds
        self._turn = turn_seconds
        self._clock = clock

    def _sweep(self) -> None:
        now = self._clock()
        expired = [cid for cid, g in self._games.items() if now - g.last_touched > self._ttl]
        for cid in expired:
            del self._games[cid]

    def _advance_turn(self, game: WordChainGame) -> None:
        for _ in range(len(game.players)):
            game.turn_pos = (game.turn_pos + 1) % len(game.players)
            if game.players[game.turn_pos].alive:
                return

    def _apply_timeouts(self, game: WordChainGame, now: float) -> bool:
        """지난 턴 마감들을 지연 판정한다. 상태가 바뀌었으면 True."""
        changed = False
        while (
            game.status == PLAYING
            and game.turn_deadline is not None
            and now > game.turn_deadline
        ):
            current = game.players[game.turn_pos]
            current.alive = False
            game.last_event = f"{current.display_name}님이 시간 초과로 탈락했어요"
            changed = True
            alive = game.alive_players()
            if len(alive) <= 1:
                game.status = FINISHED
                game.winner_user_id = alive[0].user_id if alive else None
                game.turn_deadline = None
            else:
                self._advance_turn(game)
                game.turn_deadline = game.turn_deadline + self._turn
        return changed

    def seconds_left(self, game: WordChainGame) -> int | None:
        if game.status != PLAYING or game.turn_deadline is None:
            return None
        return max(0, int(game.turn_deadline - self._clock()))

    async def join(self, channel_id: int, user_id: int, display_name: str) -> WordChainGame:
        async with self._lock:
            self._sweep()
            now = self._clock()
            game = self._games.get(channel_id)
            if game is None:
                game = WordChainGame(channel_id=channel_id, last_touched=now)
                self._games[channel_id] = game
            self._apply_timeouts(game, now)
            if game.status == FINISHED:
                # 끝난 판에 다시 들어오면 새 대기실을 연다.
                game.status = WAITING
                game.players = []
                game.words = []
                game.used = set()
                game.turn_pos = 0
                game.winner_user_id = None
                game.last_event = None
                game.turn_deadline = None
            if game.status == PLAYING and game.find_player(user_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="게임이 이미 진행 중이에요. 다음 라운드에 참여하세요",
                )
            game.last_touched = now
            if game.find_player(user_id) is None:
                game.players.append(WordChainPlayer(user_id=user_id, display_name=display_name))
            return game

    async def start(self, channel_id: int, user_id: int) -> WordChainGame:
        async with self._lock:
            self._sweep()
            game = self._games.get(channel_id)
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
            game.turn_deadline = now + self._turn
            game.last_touched = now
            first = game.players[0]
            game.last_event = f"게임 시작! {first.display_name}님이 첫 단어를 입력하세요"
            return game

    async def submit(self, channel_id: int, user_id: int, word: str) -> WordChainGame:
        async with self._lock:
            self._sweep()
            game = self._games.get(channel_id)
            if game is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="진행 중인 게임이 없어요"
                )
            now = self._clock()
            self._apply_timeouts(game, now)
            game.last_touched = now
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
                allowed = allowed_first_chars(game.words[-1].word)
                if word[0] not in allowed:
                    pretty = "/".join(sorted(allowed))
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail=f"'{pretty}'(으)로 시작하는 단어여야 해요",
                    )

            game.words.append(
                WordEntry(user_id=user_id, display_name=current.display_name, word=word)
            )
            game.used.add(word)
            game.last_event = None
            self._advance_turn(game)
            game.turn_deadline = now + self._turn
            return game

    async def get(self, channel_id: int) -> tuple[WordChainGame | None, bool]:
        """게임과 함께, 지연 타임아웃 판정으로 상태가 바뀌었는지를 돌려준다."""
        async with self._lock:
            self._sweep()
            game = self._games.get(channel_id)
            if game is None:
                return None, False
            changed = self._apply_timeouts(game, self._clock())
            return game, changed


store = WordChainStore()


def get_wordchain_store() -> WordChainStore:
    return store
