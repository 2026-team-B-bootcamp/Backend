"""채널별 빙고 게임 상태를 Redis에 저장하고 join/click(번호 호출)을 처리한다.

라우터(routers/bingo.py)가 이 store를 호출해 게임을 열고 진행시키며,
승리 판정은 services/bingo/logic.py의 count_completed_lines에 위임한다.

저장 방식: 게임 하나를 JSON으로 직렬화해 "game:bingo:{채널id}" 키에 TTL과
함께 저장한다. 방치된 게임은 Redis가 TTL 만료로 알아서 지우므로 (이전의
_sweep/last_touched 같은) 별도의 청소 로직이 필요 없고, 워커가 여러 개여도
모두 같은 상태를 본다. 동시 요청은 채널별 Redis 분산 락으로 직렬화한다
(이전의 asyncio.Lock은 한 프로세스 안에서만 유효했다).
"""

import json
from dataclasses import asdict, dataclass, field

from fastapi import HTTPException, status

from app.core.redis import get_redis
from app.services.bingo.logic import WIN_LINES, count_completed_lines, generate_board
from app.services.game_ttl import ttl_for

TTL_SECONDS = 3600

WAITING = "waiting"
PLAYING = "playing"
FINISHED = "finished"


@dataclass
class BingoPlayer:
    user_id: int
    display_name: str
    board: list[int]


@dataclass
class BingoCall:
    """누가 몇 번째로 어떤 숫자를 불렀는지 — 화면 아래 호출 기록으로 보여준다."""

    number: int
    user_id: int
    display_name: str


@dataclass
class BingoGame:
    channel_id: int
    # 대기(2명 모으는 중) → 진행 → 종료. 진행 중엔 새 참가 불가(관전만).
    status: str = WAITING
    called_numbers: set[int] = field(default_factory=set)
    players: dict[int, BingoPlayer] = field(default_factory=dict)
    winner_user_id: int | None = None
    # 몇 번째 판인지. 승자가 나온 뒤 누군가 다시 join 하면 1씩 올라간다.
    round: int = 1
    # 턴 순서(참가한 순서대로 고정된 user_id 목록)와 지금 몇 번째 차례인지.
    # 시작(start) 시점에 확정되고, 한 번 호출할 때마다 turn_index가 1씩 돈다.
    turn_order: list[int] = field(default_factory=list)
    turn_index: int = 0
    # 호출된 순서 기록 — called_numbers(집합)와 달리 "몇 번째로 누가" 불렀는지가 남는다.
    call_log: list[BingoCall] = field(default_factory=list)

    def current_turn_user_id(self) -> int | None:
        if self.status != PLAYING or not self.turn_order:
            return None
        return self.turn_order[self.turn_index % len(self.turn_order)]


def _to_json(game: BingoGame) -> str:
    data = asdict(game)
    data["called_numbers"] = sorted(game.called_numbers)  # set은 JSON에 없으므로 리스트로 변환
    return json.dumps(data)


def _from_json(raw: str) -> BingoGame:
    data = json.loads(raw)
    data["called_numbers"] = set(data["called_numbers"])
    # JSON 객체 키는 항상 문자열이므로 user_id(int)로 되돌린다.
    data["players"] = {int(k): BingoPlayer(**v) for k, v in data["players"].items()}
    # 턴제 이전에 저장된 게임에는 아래 키들이 없다 — 기본값으로 채워 KeyError를 막는다.
    data["call_log"] = [BingoCall(**c) for c in data.get("call_log", [])]
    data.setdefault("turn_order", [])
    data.setdefault("turn_index", 0)
    return BingoGame(**data)


class BingoGameStore:
    def __init__(self, ttl_seconds: float = TTL_SECONDS) -> None:
        self._ttl = ttl_seconds

    def _key(self, channel_id: int) -> str:
        return f"game:bingo:{channel_id}"

    def _lock(self, channel_id: int):
        # 같은 채널에 대한 동시 요청을 워커에 상관없이 한 줄로 세우는 분산 락.
        return get_redis().lock(f"lock:bingo:{channel_id}", timeout=10)

    async def _load(self, channel_id: int) -> BingoGame | None:
        raw = await get_redis().get(self._key(channel_id))
        return _from_json(raw) if raw else None

    async def _save(self, game: BingoGame) -> None:
        # 저장할 때마다 TTL을 다시 걸어준다 — 활동이 있는 게임은 계속 살아 있고,
        # 방치된 게임은 TTL 만료로 Redis에서 자동 소멸한다.
        await get_redis().set(
            self._key(game.channel_id),
            _to_json(game),
            # 대기·종료 상태로 방치되면 30초 뒤 자동으로 사라진다(game_ttl 참고).
            ex=ttl_for(game.status, self._ttl),
        )

    async def join(
        self, channel_id: int, user_id: int, display_name: str
    ) -> BingoGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None:
                game = BingoGame(channel_id=channel_id)
            elif game.status == FINISHED:
                # 끝난 판에 다시 들어오면 새 대기실을 연다 (다음 라운드).
                game.status = WAITING
                game.called_numbers = set()
                game.players = {}
                game.winner_user_id = None
                game.turn_order = []
                game.turn_index = 0
                game.call_log = []
                game.round += 1
            # 진행 중이면 새 참가 불가 — 관전만 (이미 참가자는 그대로 통과해 상태만 받는다).
            if game.status == PLAYING and user_id not in game.players:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="이미 진행 중이에요. 관전만 할 수 있어요",
                )
            if user_id not in game.players:
                # 처음 참가하는 유저에게만 새 보드를 발급한다 (재접속 시 기존 보드 유지).
                game.players[user_id] = BingoPlayer(
                    user_id=user_id, display_name=display_name, board=generate_board()
                )
            await self._save(game)
            return game

    async def start(self, channel_id: int, user_id: int) -> BingoGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None or user_id not in game.players:
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
            game.status = PLAYING
            # 참가한 순서를 그대로 턴 순서로 삼는다(dict는 삽입 순서를 유지한다).
            # 시작 버튼을 누른 사람이 아니라 먼저 참가한 사람부터 시작한다.
            game.turn_order = list(game.players.keys())
            game.turn_index = 0
            await self._save(game)
            return game

    async def click(self, channel_id: int, user_id: int, number: int) -> BingoGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None or user_id not in game.players:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Not a player in this game",
                )
            if game.status != PLAYING:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="진행 중인 게임이 아니에요",
                )
            # 턴제: 자기 차례가 아니면 부를 수 없다. 예전에는 아무나 아무 때나 눌러서
            # 빠른 사람이 판을 독식했다 — 오목·틱택토와 같은 방식으로 맞췄다.
            turn_user_id = game.current_turn_user_id()
            if turn_user_id is not None and turn_user_id != user_id:
                current = game.players.get(turn_user_id)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"지금은 {current.display_name if current else '상대'}님 차례예요",
                )
            if number in game.called_numbers:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="이미 호출된 숫자예요"
                )

            # 번호를 호출 목록에 추가하면, 이 번호를 가진 모든 플레이어의 보드에
            # 자동으로 반영된다(마킹 여부는 called_numbers와의 비교로 계산되므로).
            game.called_numbers.add(number)
            caller = game.players[user_id]
            game.call_log.append(
                BingoCall(number=number, user_id=user_id, display_name=caller.display_name)
            )
            # 다음 사람 차례로 넘긴다. 승부가 나면 아래에서 status가 FINISHED가 되므로
            # current_turn_user_id()가 None을 돌려주고 turn_index는 더 이상 쓰이지 않는다.
            game.turn_index = (game.turn_index + 1) % max(len(game.turn_order), 1)

            # 아직 승자가 없다면, 모든 플레이어의 완성 줄 수를 다시 계산해
            # WIN_LINES(3줄) 이상 달성한 사람이 있는지 확인한다.
            completed = {
                pid: count_completed_lines(player.board, game.called_numbers)
                for pid, player in game.players.items()
            }
            if game.winner_user_id is None:
                qualified = [pid for pid, lines in completed.items() if lines >= WIN_LINES]
                if qualified:
                    # 방금 클릭한 사람이 동시에 조건을 채웠다면 그 사람을 우선한다.
                    game.winner_user_id = (
                        user_id if user_id in qualified else qualified[0]
                    )
                    game.status = FINISHED
            await self._save(game)
            return game

    async def get(self, channel_id: int) -> BingoGame | None:
        async with self._lock(channel_id):
            return await self._load(channel_id)

    async def status(self, channel_id: int) -> str:
        # 관전 유도용 상태: 없음 / 대기 / 진행중 / 종료
        game = await self._load(channel_id)
        return game.status if game else "none"


store = BingoGameStore()


def get_bingo_store() -> BingoGameStore:
    return store
