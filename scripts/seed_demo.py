"""데모/발표용 목데이터 시드.

실행: cd Backend && uv run python scripts/seed_demo.py
- 가짜 유저 6명(비밀번호 전부 test1234) + 서버 2개 + 채널/태그/대화를 만든다.
- demo1/demo2 계정을 모든 서버에 참여시키고 표시 이름을 자연스럽게 바꾼다.
- 메시지 타임스탬프를 어제~오늘로 분산시켜 날짜 구분선/그룹핑이 보이게 한다.
- 태그는 단어가 아닌 문구형("롤 티어 실버4", "맛집탐방(특히 라멘맛집)")으로,
  유사도 매칭(pgvector)이 돋보이도록 설계했고 시드 후 임베딩까지 생성한다
  (GEMINI_API_KEY 없으면 완전일치만 동작).
- 데모 서버(BOOT3CMP/MEETUP01)가 이미 있으면 지우고 새 데이터로 다시 만든다.

문구형 태그 유사쌍 실측값 (gemini-embedding-001, 임계값 0.84):
  롤 티어 실버4↔롤 골드 승급전 중 0.877 | 보드게임 좋아함↔보드게임 30종 보유 0.875
  조기축구회 활동 중↔동네 풋살 뛰어요 0.870 | 여행 좋아함(최근엔 교토)↔여행 계획 짜는 게 취미 0.858
  조기축구회 활동 중↔아침 러닝 크루 0.854 | 맛집탐방(특히 라멘맛집)↔카페투어 도장깨기 0.853
  핸드드립 커피 내려 마심↔커피 하루 3잔 0.851 | 주말마다 등산 다녀요↔캠핑·백패킹 다님 0.849
  포켓몬 시리즈 전부 클리어↔피카츄 굿즈 모음 0.844 | 영화 정주행(넷플릭스파)↔영화관 자주 가는 편 0.842
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.base import async_session_maker  # noqa: E402
from app.models import Channel, Message, Server, ServerMember, Tag, User  # noqa: E402
from app.services import tag_service  # noqa: E402

PASSWORD = "test1234"

# key → (email, 표시이름)
USERS = {
    "sujin": ("sujin@test.com", "수진"),
    "minho": ("minho@test.com", "민호"),
    "jiwoo": ("jiwoo@test.com", "지우"),
    "haneul": ("haneul@test.com", "하늘"),
    "doyun": ("doyun@test.com", "도윤"),
    "yuna": ("yuna@test.com", "유나"),
    "demo1": ("demo1@test.com", "준영"),
    "demo2": ("demo2@test.com", "서연"),
}

# 문구형 태그 — 완전히 같은 문자열이 없어도 유사도로 이어지는 게 포인트.
# 두 서버에서 같은 프로필을 쓴다 (같은 사람이 같은 관심사를 등록하는 게 자연스럽다).
PROFILE_TAGS = {
    "sujin": ("주말마다 등산 다녀요", "핸드드립 커피 내려 마심", "홈베이킹(마들렌 장인)"),
    "minho": ("조기축구회 활동 중", "롤 티어 실버4", "보드게임 좋아함"),
    "jiwoo": ("캠핑·백패킹 다님", "필름카메라로 사진 찍음", "재즈 플레이리스트 만듦"),
    "haneul": ("아침 러닝 크루", "맛집탐방(특히 라멘맛집)", "영화 정주행(넷플릭스파)"),
    "doyun": ("포켓몬 시리즈 전부 클리어", "보드게임 30종 보유", "헬스 3년차"),
    "yuna": ("여행 좋아함(최근엔 교토)", "카페투어 도장깨기", "독서모임 나감"),
    "demo1": ("동네 풋살 뛰어요", "커피 하루 3잔", "롤 골드 승급전 중"),
    "demo2": ("피카츄 굿즈 모음", "영화관 자주 가는 편", "여행 계획 짜는 게 취미"),
}

# (작성자, 내용) — 순서대로 시간 간격을 두고 삽입된다. None은 시간 점프(블록 구분).
BOOTCAMP_GENERAL = [
    ("sujin", "다들 안녕하세요! 오늘 첫 모임 너무 반가웠어요 🙌"),
    ("minho", "고생하셨습니다~ 생각보다 사람들이 다 재밌으셔서 다행"),
    ("jiwoo", "저 오늘 늦게 합류했는데 자기소개 채널에 올리면 되나요?"),
    ("sujin", "넵넵 #자기소개 에 편하게 올려주세요!"),
    ("demo1", "수진님 태그에 '홈베이킹(마들렌 장인)'이라고 써두셨던데 실화인가요"),
    ("sujin", "ㅋㅋㅋ 장인까지는 과장이고 마들렌은 잘 굽습니다"),
    ("sujin", "다음 모임 때 구워올게요 진짜로"),
    ("doyun", "우와 그럼 저는 보드게임 가져오겠습니다. 6인용 있어요"),
    ("haneul", "분위기 너무 좋다"),
    (None, None),  # 시간 점프
    ("yuna", "혹시 내일 스터디 몇 시부터였죠?"),
    ("minho", "10시요! 늦으면 커피 사기입니다"),
    ("yuna", "헉 알람 3개 맞춰놓겠습니다"),
    ("demo2", "저도 내일부터 합류해요~ 잘 부탁드립니다!"),
    ("sujin", "서연님 환영해요!! 태그 보니 영화관 자주 가시네요, 저번주에 뭐 보셨어요?"),
    ("demo2", "듄2 봤어요 완전 인생영화... 하늘님도 영화 태그 있으시던데 보셨나요?"),
    ("haneul", "정주행파지만 그건 아이맥스로 두 번 봤습니다"),
    ("demo2", "와 동지다. 방금 보니까 우리 태그도 ✦ 로 이어져 있네요"),
    ("minho", "저는 준영님 '동네 풋살 뛰어요'가 제 '조기축구회 활동 중'이랑 겹친다고 떠서 신기했어요. 문장이 달라도 알아보네"),
    ("demo1", "오 그러네요 ㅋㅋ 민호님 주말에 풋살 한 판 어때요"),
    (None, None),
    ("minho", "오늘 점심 이야기는 #점심메뉴 로 옮깁시다 ㅋㅋ 여기가 밥 얘기로 도배됨"),
    ("doyun", "ㅋㅋㅋ인정. 근데 마지막으로 하나만... 학식 돈까스 실화냐"),
    ("minho", "돈까스 얘기 금지"),
    ("jiwoo", "오늘 세션 자료 공유해주실 분 있나요? 필기를 놓쳤어요 ㅠ"),
    ("yuna", "제가 정리한 거 스터디 채널에 올려둘게요!"),
    ("jiwoo", "유나님 최고... 🙏"),
    ("demo1", "이따 6시에 끝말잇기 한 판 어때요? 오른쪽 게임 탭에서 바로 됩니다"),
    ("doyun", "콜. 지는 사람 내일 커피"),
    ("minho", "저 끝말잇기 전국대회급인데 후회하지 마세요"),
]

BOOTCAMP_INTRO = [
    ("sujin", "안녕하세요, 수진입니다! 주말마다 산에 가고 평일엔 홈베이킹 해요. 커피는 원두부터 갈아서 핸드드립으로 ☕ 잘 부탁드려요!"),
    ("minho", "민호입니다. 일요일마다 조기축구 뛰고, 롤은 실버4지만 마음은 챌린저입니다. 보드게임도 환영"),
    ("jiwoo", "지우예요~ 캠핑이랑 백패킹 다니면서 필름카메라로 사진 찍는 게 취미입니다. 재즈 좋아하시는 분 플리 공유해요 🎷"),
    ("haneul", "하늘입니다! 아침 러닝 크루 하고 있고, 주말엔 라멘집 도장깨기 다녀요. 넷플릭스 정주행이 유일한 휴식"),
    ("doyun", "도윤이라고 합니다. 보드게임 30종 보유 중... 포켓몬은 게임보이 시절부터 전 시리즈 클리어했습니다. 헬스는 재미로"),
    ("yuna", "유나입니다 ✈️ 최근에 교토 다녀왔고, 동네 카페 도장깨기 하는 중이에요. 독서모임도 나가요. 책 추천 환영!"),
    ("demo1", "준영입니다! 동네 풋살팀에서 뛰고 있고 롤은 골드 승급전 중... 커피는 하루 3잔입니다. 다들 친하게 지내요~"),
    ("demo2", "서연이에요. 영화관 자주 가고 여행 계획 짜는 게 취미예요. 피카츄 굿즈 모으는 중 ⚡ 잘 부탁드려요 :)"),
]

BOOTCAMP_LUNCH = [
    ("minho", "오늘 점심 국밥 vs 파스타"),
    ("doyun", "국밥"),
    ("haneul", "국밥"),
    ("yuna", "파스타... 는 저 혼자인가요"),
    ("sujin", "유나님 저랑 파스타 가요 ㅋㅋ"),
    ("minho", "그럼 국밥팀 12시 로비 집합"),
    ("demo1", "국밥팀 +1"),
    ("jiwoo", "저는 도시락인데 같이 앉을 자리만 맡아주세요"),
    ("doyun", "오케이 창가 쪽 잡아둘게요"),
]

BOOTCAMP_STUDY = [
    ("yuna", "오늘 세션 정리본 올립니다! 틀린 부분 있으면 알려주세요 📝"),
    ("jiwoo", "감사합니다 덕분에 살았어요"),
    ("demo1", "3번 문제 풀이 방식 이거 맞나요? 저는 다르게 풀었는데"),
    ("yuna", "오 그 방법도 되네요! 내일 스터디에서 같이 봐요"),
    ("minho", "내일 10시 늦지 마세요 여러분. 늦으면 커피입니다 (2번째 공지)"),
]

# 스토리 아크: 어색한 인사 → 문구형 태그 설정 → 유사 태그(✦)가 서로를 이어줌
#            → AI 아이스브레이커로 말문 트기 → 끝말잇기 한 판 → 친해짐
MEETUP_GENERAL = [
    ("sujin", "다들 환영해요! 오늘 서로 처음 뵙는 분들이 대부분이죠? 👋"),
    ("sujin", "우선 프로필에서 관심사 태그 3개부터 설정해주세요. 단어 말고 '롤 티어 실버4'처럼 편하게 문장으로 쓰셔도 돼요"),
    ("sujin", "문구가 서로 달라도 비슷한 관심사면 알아서 ✦ 로 이어줍니다!"),
    ("doyun", "안녕하세요… 이런 모임 처음이라 뭐라고 인사해야 할지 모르겠네요 ㅎㅎ"),
    ("yuna", "저도요.. 일단 반갑습니다"),
    ("haneul", "(조용히 태그 설정하고 옴)"),
    (None, None),  # 태그 설정 시간
    ("demo2", "어?? 저 '피카츄 굿즈 모음'이라고 적었는데 도윤님 '포켓몬 시리즈 전부 클리어'랑 겹친다고 ✦ 떠요 ㅋㅋㅋ"),
    ("demo2", "문장이 완전 다른데도 알아보네요 신기해"),
    ("doyun", "포켓몬 동지다!!! 서연님 굿즈 모으시면 혹시 하트골드 한정판도 아세요? 저 실물 소장 중입니다"),
    ("demo2", "헉 실물 소장자라니... 갑자기 할 말이 산더미네요"),
    ("minho", "저는 그냥 '보드게임 좋아함'이라고만 썼는데 도윤님 '보드게임 30종 보유'랑 이어주네요. 루미큐브 있으세요?"),
    ("doyun", "루미큐브 없는 보드게임러가 있나요. 다음 모임 때 가져오겠습니다"),
    ("jiwoo", "저는 아직 뭐라 말 걸지 몰라서 AI 버튼 눌러봤어요 ㅋㅋ 이 질문 AI가 만들어준 거예요 →"),
    ("jiwoo", "\"유나님 최근에 교토 다녀오셨다면서요? 제일 좋았던 곳 하나만 꼽는다면 어디예요?\""),
    ("yuna", "ㅋㅋㅋㅋ 제 태그에 교토 써둔 걸 질문에 녹였네요? 후시미 이나리요! 지우님은 캠핑 다니시면서 사진도 찍으시죠"),
    ("jiwoo", "네 필름카메라 들고 다녀요 📸 근데 제 '캠핑·백패킹 다님'이 수진님 '주말마다 등산 다녀요'랑도 ✦ 로 이어져 있더라고요"),
    ("sujin", "오 맞아요 ㅋㅋ 산에서 자는 사람과 산에 오르는 사람의 만남... 다음 달 백패킹 같이 가요 우리"),
    ("demo1", "저도 AI 질문 하나 써봅니다. \"수진님, 핸드드립으로 내려 마시면 어떤 원두 좋아하세요? 입문자한테 추천해주실 만한 것도 궁금해요\" ...진짜 궁금해서요. 저는 아직 하루 3잔 믹스파입니다"),
    ("sujin", "드디어 커피 동지가!! 성수동에 아는 로스터리 있는데 이따 링크 드릴게요"),
    ("minho", "준영님 잠깐만요. 우리 태그 두 개나 겹치는데요? 축구↔풋살에 롤까지. 실버4인데 골드 승급전이시라니 듀오 하시죠"),
    ("demo1", "ㅋㅋㅋㅋ 두 개 겹치는 건 처음 봤네요. 콜, 오늘 밤에 한 판 가시죠"),
    ("haneul", "저도 AI가 만들어준 질문으로 실례합니다. \"도윤님, 포켓몬 전 시리즈 클리어하셨다니 대단해요. 인생작 하나만 꼽는다면요?\""),
    ("doyun", "하늘님까지... 오늘 포켓몬 얘기로 밤새겠는데요? 인생작은 당연히 하트골드입니다"),
    ("yuna", "하늘님 저희도 ✦ 떴어요. 라멘 맛집탐방이랑 제 카페투어를 이어주네요 ㅋㅋ 먹으러 다니는 사람끼리 알아본다"),
    ("haneul", "먹지도 마시지도 못하는 태그가 없죠 저희는. 라멘 먼저 갔다가 카페 가는 코스 짭시다"),
    (None, None),  # 저녁 식사 후
    ("haneul", "분위기 풀린 김에 끝말잇기 한 판 어때요? 오른쪽 미니게임 탭에서 바로 되던데"),
    ("doyun", "콜. 진 사람이 다음 모임 간식 어떻습니까"),
    ("demo2", "저도 껴주세요! 포켓몬 이름으로만 이어가고 싶지만 참을게요"),
    (None, None),  # 게임 한 판
    ("haneul", "도윤님 아까 '력'에서 '역'으로 받은 거 뭐예요;; 두음법칙까지 쓰시네"),
    ("doyun", "보드게임러의 승부욕입니다. 간식은 하늘님 담당이 되었습니다"),
    ("haneul", "억울하지만 인정 ㅋㅋ 다음엔 빙고로 복수함"),
    ("yuna", "한 시간 전엔 서로 '안녕하세요..'만 하던 사람들이 지금 게임으로 싸우는 중 ㅋㅋㅋ"),
    ("sujin", "이게 바로 태그의 힘이죠 😎 문장으로 대충 써도 알아서 이어주니까 좋네요. 다음 주엔 오프라인에서 봬요!"),
    ("demo2", "처음 만난 날인데 벌써 편해진 게 신기하네요. 다음 주에 봬요~"),
]

MEETUP_INTEREST = [
    ("sujin", "커피 얘기는 여기서 이어가요 ☕ 아까 말한 성수동 로스터리 → '커피상점 이심' 입니다. 준영님 여기 원두 산미 적어서 믹스파 입문용으로 좋아요"),
    ("demo1", "오 저장했습니다. 주말에 가봐야겠다"),
    ("yuna", "카페투어러로서 저도 갑니다. 하늘님 근처 라멘집도 찾아두세요, 코스로 돌죠"),
    ("haneul", "성수동이면 이미 리스트 있습니다. 라멘 → 커피 → 산책 풀코스 짜올게요"),
    ("doyun", "포켓몬팀(?)은 여기로 모여주세요. 서연님 하트골드 한정판 실물 다음 모임 때 가져옵니다"),
    ("demo2", "실물 영접이라니... 굿즈 몇 개 들고 가서 자랑할게요 ⚡"),
    ("jiwoo", "등산×백패킹 연합은 다음 달 어떠세요? 수진님 코스 잘 아시죠?"),
    ("sujin", "청계산 야영장 코스 추천해요. 사진 스팟도 많아서 지우님 필름카메라 들고 오시면 됩니다"),
    ("minho", "풋살은 이번 주 토요일 10시 어떠세요 준영님. 사람 더 모아서 5:5 가능할 듯"),
    ("demo1", "콜입니다. 하늘님도 러닝 크루시니까 체력 되실 테고... 같이 하실래요?"),
    ("haneul", "러닝이랑 풋살은 다른 운동인데요?? ...간다는 뜻입니다"),
]

DEMO_GENERAL = [
    ("demo2", "준영아 이 서비스 봤어? 태그를 문장으로 써도 비슷한 사람 이어주더라"),
    ("demo1", "ㅇㅇ 첫모임 라운지에서 민호님이랑 나랑 축구·롤 두 개 겹친다고 ✦ 뜬 거 봤어? 문구가 다 다른데"),
    ("demo2", "그러니까 ㅋㅋ AI가 말 걸 질문도 만들어주던데 그걸로 대화 텄어"),
    ("demo2", "끝말잇기 한 판? 내가 이기면 커피"),
    ("demo1", "콜. 근데 나 두음법칙까지 마스터함"),
]


async def get_or_create_user(db, email: str, name: str) -> User:
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, password_hash=hash_password(PASSWORD), display_name=name)
        db.add(user)
        await db.flush()
    elif user.display_name != name:
        user.display_name = name
    return user


async def ensure_member(db, server_id: int, user_id: int) -> None:
    exists = await db.scalar(
        select(ServerMember).where(
            ServerMember.server_id == server_id, ServerMember.user_id == user_id
        )
    )
    if exists is None:
        db.add(ServerMember(server_id=server_id, user_id=user_id))


async def ensure_tags(db, server_id: int, user_id: int, tags: tuple[str, str, str]) -> None:
    tag = await db.scalar(
        select(Tag).where(Tag.server_id == server_id, Tag.user_id == user_id)
    )
    if tag is None:
        db.add(Tag(server_id=server_id, user_id=user_id, tag1=tags[0], tag2=tags[1], tag3=tags[2]))


def timeline(script: list, start: datetime) -> list[tuple[str, str, datetime]]:
    """(작성자, 내용) 목록에 자연스러운 타임스탬프를 붙인다. None 행은 45~70분 점프."""
    gaps = [1, 2, 1, 3, 2, 1, 4, 2]  # 분 단위, 순환
    out: list[tuple[str, str, datetime]] = []
    t = start
    gi = 0
    for author, content in script:
        if author is None:
            t += timedelta(minutes=45 + (gi % 3) * 12)
            continue
        out.append((author, content, t))
        t += timedelta(minutes=gaps[gi % len(gaps)], seconds=(gi * 17) % 50)
        gi += 1
    return out


async def seed_channel(db, channel_id: int, users: dict[str, User], script: list, start: datetime) -> int:
    count = 0
    for author, content, ts in timeline(script, start):
        db.add(
            Message(
                channel_id=channel_id,
                user_id=users[author].id,
                content=content,
                created_at=ts,
            )
        )
        count += 1
    return count


async def reset_server(db, invite_code: str) -> None:
    """같은 초대코드의 기존 데모 서버를 지운다 (채널/메시지/태그/멤버는 FK cascade로 함께 삭제)."""
    await db.execute(delete(Server).where(Server.invite_code == invite_code))
    await db.flush()


async def main() -> None:
    now = datetime.now(UTC)
    yesterday = (now - timedelta(days=1)).replace(hour=4, minute=40, second=0, microsecond=0)
    today_early = now - timedelta(hours=3, minutes=20)
    recent = now - timedelta(minutes=42)

    async with async_session_maker() as db:
        users = {key: await get_or_create_user(db, email, name) for key, (email, name) in USERS.items()}
        await db.flush()
        total = 0

        # ── 서버 1: 시흥 부트캠프 3기 ─────────────────────────
        await reset_server(db, "BOOT3CMP")
        boot = Server(name="시흥 부트캠프 3기", invite_code="BOOT3CMP", created_by=users["sujin"].id)
        db.add(boot)
        await db.flush()
        boot_channels: dict[str, Channel] = {}
        for ch_name in ["일반", "자기소개", "점심메뉴", "스터디"]:
            ch = Channel(server_id=boot.id, name=ch_name)
            db.add(ch)
            boot_channels[ch_name] = ch
        await db.flush()

        for key in USERS:
            await ensure_member(db, boot.id, users[key].id)
            await ensure_tags(db, boot.id, users[key].id, PROFILE_TAGS[key])

        total += await seed_channel(db, boot_channels["자기소개"].id, users, BOOTCAMP_INTRO, yesterday)
        total += await seed_channel(db, boot_channels["일반"].id, users, BOOTCAMP_GENERAL, yesterday + timedelta(hours=1))
        total += await seed_channel(db, boot_channels["점심메뉴"].id, users, BOOTCAMP_LUNCH, today_early)
        total += await seed_channel(db, boot_channels["스터디"].id, users, BOOTCAMP_STUDY, today_early + timedelta(hours=1))

        # ── 서버 2: 첫모임 라운지 (유사 태그 + AI 아이스브레이커 스토리) ──
        await reset_server(db, "MEETUP01")
        meetup = Server(name="첫모임 라운지", invite_code="MEETUP01", created_by=users["sujin"].id)
        db.add(meetup)
        await db.flush()
        meetup_general = Channel(server_id=meetup.id, name="일반")
        meetup_interest = Channel(server_id=meetup.id, name="관심사 수다")
        db.add_all([meetup_general, meetup_interest])
        await db.flush()

        for key in USERS:
            await ensure_member(db, meetup.id, users[key].id)
            await ensure_tags(db, meetup.id, users[key].id, PROFILE_TAGS[key])

        # 어제 저녁 19시(KST)부터 시작하는 첫 만남 아크
        total += await seed_channel(db, meetup_general.id, users, MEETUP_GENERAL, yesterday + timedelta(hours=5, minutes=20))
        total += await seed_channel(db, meetup_interest.id, users, MEETUP_INTEREST, today_early + timedelta(minutes=40))

        # ── 기존 '데모 서버'가 있으면 짧은 대화 추가 ─────────
        demo_srv = await db.scalar(select(Server).where(Server.name == "데모 서버"))
        if demo_srv is not None:
            first_ch = await db.scalar(
                select(Channel).where(Channel.server_id == demo_srv.id).order_by(Channel.id)
            )
            if first_ch is not None:
                existing = await db.scalar(
                    select(Message.id).where(Message.channel_id == first_ch.id).limit(1)
                )
                if existing is None:
                    total += await seed_channel(db, first_ch.id, users, DEMO_GENERAL, recent)

        await db.commit()

        # ── 태그 임베딩 생성 (유사도 매칭 활성화) ─────────────
        # 라우터를 거치지 않고 Tag를 직접 넣었으므로 임베딩을 여기서 채운다.
        # 이미 임베딩된 태그 텍스트는 API를 다시 부르지 않는다.
        all_tags = sorted({t for tags in PROFILE_TAGS.values() for t in tags})
        if settings.gemini_api_key:
            await tag_service.ensure_embeddings(db, all_tags)
            print(f"태그 임베딩 준비 완료: {len(all_tags)}종 (유사 태그 ✦ 매칭 활성화)")
        else:
            print("GEMINI_API_KEY가 없어 임베딩을 건너뜀 — 태그는 완전일치로만 매칭됩니다")

        print(f"시드 완료: 유저 {len(users)}명, 서버 2개(+데모 서버 보강), 메시지 {total}개")
        print("모든 목업 계정 비밀번호: test1234 (예: sujin@test.com)")


if __name__ == "__main__":
    asyncio.run(main())
