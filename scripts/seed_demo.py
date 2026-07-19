"""데모/발표용 목데이터 시드.

실행: cd Backend && uv run python scripts/seed_demo.py
- 가짜 유저 6명(비밀번호 전부 test1234) + 서버 2개 + 채널/태그/대화를 만든다.
- demo1/demo2 계정을 모든 서버에 참여시키고 표시 이름을 자연스럽게 바꾼다.
- 메시지 타임스탬프를 어제~오늘로 분산시켜 날짜 구분선/그룹핑이 보이게 한다.
- 이미 시드된 경우(초대코드 존재) 건너뛴다.
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.base import async_session_maker  # noqa: E402
from app.models import Channel, Message, Server, ServerMember, Tag, User  # noqa: E402

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

BOOTCAMP_TAGS = {
    "sujin": ("등산", "커피", "베이킹"),
    "minho": ("축구", "커피", "롤"),
    "jiwoo": ("캠핑", "재즈", "사진"),
    "haneul": ("러닝", "요리", "영화"),
    "doyun": ("보드게임", "롤", "헬스"),
    "yuna": ("여행", "카페투어", "독서"),
    "demo1": ("축구", "게임", "커피"),
    "demo2": ("영화", "게임", "여행"),
}

# 첫모임 라운지 — 서로 처음 만난 사람들이 태그로 빠르게 친해지는 과정을 보여주는 서버
MEETUP_TAGS = {
    "sujin": ("커피", "등산", "베이킹"),
    "minho": ("축구", "롤", "루미큐브"),
    "jiwoo": ("사진", "여행", "재즈"),
    "haneul": ("러닝", "요리", "게임"),
    "doyun": ("보드게임", "헬스", "맥주"),
    "yuna": ("여행", "독서", "카페투어"),
    "demo1": ("커피", "게임", "축구"),
    "demo2": ("영화", "여행", "게임"),
}

# (작성자, 내용) — 순서대로 시간 간격을 두고 삽입된다. None은 시간 점프(블록 구분).
BOOTCAMP_GENERAL = [
    ("sujin", "다들 안녕하세요! 오늘 첫 모임 너무 반가웠어요 🙌"),
    ("minho", "고생하셨습니다~ 생각보다 사람들이 다 재밌으셔서 다행"),
    ("jiwoo", "저 오늘 늦게 합류했는데 자기소개 채널에 올리면 되나요?"),
    ("sujin", "넵넵 #자기소개 에 편하게 올려주세요!"),
    ("demo1", "수진님 태그 보니까 베이킹 하시네요?? 마들렌 구울 줄 아시면 존경합니다"),
    ("sujin", "ㅋㅋㅋ 마들렌은 기본이죠"),
    ("sujin", "다음 모임 때 구워올게요 진짜로"),
    ("doyun", "우와 그럼 저는 보드게임 가져오겠습니다. 6인용 있어요"),
    ("haneul", "분위기 너무 좋다"),
    (None, None),  # 시간 점프
    ("yuna", "혹시 내일 스터디 몇 시부터였죠?"),
    ("minho", "10시요! 늦으면 커피 사기입니다"),
    ("yuna", "헉 알람 3개 맞춰놓겠습니다"),
    ("demo2", "저도 내일부터 합류해요~ 잘 부탁드립니다!"),
    ("sujin", "서연님 환영해요!! 태그에 영화 있으시네요, 저번주에 뭐 보셨어요?"),
    ("demo2", "듄2 봤어요 완전 인생영화... 하늘님도 영화 태그 있으시던데 보셨나요?"),
    ("haneul", "당연하죠 아이맥스로 두 번 봤습니다"),
    ("demo2", "와 동지다"),
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
    ("sujin", "안녕하세요, 수진입니다! 주말마다 산에 가고 평일엔 홈베이킹 해요. 커피는 원두부터 갈아 마시는 편 ☕ 잘 부탁드려요!"),
    ("minho", "민호입니다. 축구는 보는 것도 하는 것도 다 좋아하고, 롤은 실버지만 마음은 챌린저입니다. 커피 얘기 환영"),
    ("jiwoo", "지우예요~ 캠핑 다니면서 사진 찍는 게 취미입니다. 재즈 좋아하시는 분 있으면 플리 공유해요 🎷"),
    ("haneul", "하늘입니다! 아침 러닝 크루 하고 있고 요리 유튜브 보는 게 낙이에요. 영화는 장르 안 가립니다"),
    ("doyun", "도윤이라고 합니다. 보드게임 30개 보유 중... 관심 있으시면 언제든지. 헬스는 3대 신경 안 씁니다 재미로 해요"),
    ("yuna", "유나입니다 ✈️ 방학마다 여행 다니고, 동네 카페 도장깨기 하는 중이에요. 책 추천 환영합니다!"),
    ("demo1", "준영입니다! 축구랑 게임 좋아하고 커피는 하루 3잔... 다들 친하게 지내요~"),
    ("demo2", "서연이에요. 영화/여행 얘기라면 밤새 가능합니다. 잘 부탁드려요 :)"),
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

# 스토리 아크: 어색한 인사 → 태그 설정 → 태그 보고 말 걸기 → AI 질문 활용
#            → 공통 관심사(✦) 발견 → 끝말잇기 한 판 → 친해짐
MEETUP_GENERAL = [
    ("sujin", "다들 환영해요! 오늘 서로 처음 뵙는 분들이 대부분이죠? 👋"),
    ("sujin", "우선 프로필에서 관심사 태그 3개부터 설정해주세요. 이름 옆에 붙어서 서로 말 걸기 쉬워져요"),
    ("doyun", "안녕하세요… 이런 모임 처음이라 뭐라고 인사해야 할지 모르겠네요 ㅎㅎ"),
    ("yuna", "저도요.. 일단 반갑습니다"),
    ("haneul", "(조용히 태그 설정하고 옴)"),
    (None, None),  # 태그 설정 시간
    ("minho", "오 이름 옆에 태그 뜨는 거 신기하네요. 도윤님 보드게임??"),
    ("minho", "저 루미큐브 진짜 좋아하는데"),
    ("doyun", "헉 루미큐브 아세요?? 갑자기 할 말이 생겼다"),
    ("minho", "다음 모임 때 가져오세요. 제가 상대해드림"),
    ("doyun", "좋습니다 ㅋㅋ 방금까지 어색했는데 순식간이네"),
    ("jiwoo", "저는 아직 뭐라 말 걸지 몰라서 AI 버튼 눌러봤어요 ㅋㅋ 이 질문 AI가 만들어준 거예요 →"),
    ("jiwoo", "\"유나님, '여행'에 관심 있으시다고 들었어요. 최근에 다녀온 곳 중 최고는 어디였어요?\""),
    ("yuna", "ㅋㅋㅋㅋ 질문 되게 자연스러운데요? 작년에 간 교토요! 지우님 사진 태그 있으신 거 보니 여행 사진도 찍으시죠?"),
    ("jiwoo", "네 카메라 들고 다녀요 📸 교토 사진 있으면 저도 보고 싶어요"),
    ("demo2", "잠깐만요 저도 교토 다녀왔는데!! 방금 유나님 태그 옆에 ✦ 뜬 거 봤어요, 우리 여행 겹치네요"),
    ("yuna", "오 진짜다 ✦ ㅋㅋ 겹치는 태그 표시되니까 괜히 반갑네요"),
    ("demo1", "그럼 저는 커피 태그 보고 말 걸어봅니다. 수진님 원두 어디서 사세요? 요즘 홈카페 입문했는데 뭘 사야 할지 모르겠어요"),
    ("sujin", "오!! 드디어 커피 동지가 나타났다. 성수동에 아는 로스터리 있는데 이따 링크 드릴게요"),
    ("demo1", "감사합니다 🙏 태그 하나로 대화가 이렇게 쉽게 시작되네"),
    (None, None),  # 저녁 식사 후
    ("haneul", "분위기 풀린 김에 끝말잇기 한 판 어때요? 오른쪽 미니게임 탭에서 바로 되던데"),
    ("doyun", "콜. 진 사람이 다음 모임 간식 어떻습니까"),
    ("demo2", "저도 껴주세요! 게임 태그 단 보람이 있어야지"),
    (None, None),  # 게임 한 판
    ("haneul", "도윤님 아까 '력'에서 '역'으로 받은 거 뭐예요;; 두음법칙까지 쓰시네"),
    ("doyun", "보드게임러의 승부욕입니다. 간식은 하늘님 담당이 되었습니다"),
    ("haneul", "억울하지만 인정 ㅋㅋ 다음엔 빙고로 복수함"),
    ("yuna", "한 시간 전엔 서로 '안녕하세요..'만 하던 사람들이 지금 게임으로 싸우는 중 ㅋㅋㅋ"),
    ("sujin", "이게 바로 태그의 힘이죠 😎 다음 주엔 오프라인에서 봬요 다들!"),
    ("demo2", "처음 만난 날인데 벌써 편해진 게 신기하네요. 다음 주에 봬요~"),
]

MEETUP_INTEREST = [
    ("sujin", "커피 얘기는 여기서 이어가요 ☕ 아까 말한 성수동 로스터리 → '커피상점 이심' 입니다. 준영님 여기 원두 산미 적어서 입문용으로 좋아요"),
    ("demo1", "오 저장했습니다. 주말에 가봐야겠다"),
    ("yuna", "여행팀은 교토 사진 여기로 올려주세요!! 기다리는 중"),
    ("jiwoo", "정리해서 올릴게요 📸 서연님도 교토 어디 가셨는지 궁금해요"),
    ("demo2", "저는 아라시야마 쪽이요! 대나무숲이 진짜 좋았어요"),
    ("minho", "보드게임 모임은 따로 채널 팔까요? 루미큐브 + 도윤님 컬렉션이면 정기전 가능"),
    ("doyun", "30종 보유 중입니다. 언제든 환영"),
]

DEMO_GENERAL = [
    ("demo2", "준영아 이 서비스 봤어? 태그 붙는 거 신기하다"),
    ("demo1", "ㅇㅇ 우리 게임 태그 겹치는 거 봤어? ✦ 뜨는 거 귀엽네"),
    ("demo2", "끝말잇기 한 판? 내가 이기면 커피"),
    ("demo1", "콜. 근데 나 두음법칙까지 마스터함"),
    ("demo2", "그건 해봐야 알지 ㅋㅋ 오른쪽 탭 열어"),
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
        boot_exists = await db.scalar(select(Server).where(Server.invite_code == "BOOT3CMP"))
        if boot_exists is None:
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
                await ensure_tags(db, boot.id, users[key].id, BOOTCAMP_TAGS[key])

            total += await seed_channel(db, boot_channels["자기소개"].id, users, BOOTCAMP_INTRO, yesterday)
            total += await seed_channel(db, boot_channels["일반"].id, users, BOOTCAMP_GENERAL, yesterday + timedelta(hours=1))
            total += await seed_channel(db, boot_channels["점심메뉴"].id, users, BOOTCAMP_LUNCH, today_early)
            total += await seed_channel(db, boot_channels["스터디"].id, users, BOOTCAMP_STUDY, today_early + timedelta(hours=1))

        # ── 서버 2: 첫모임 라운지 (태그로 처음 만나 친해지는 스토리) ──
        meetup_exists = await db.scalar(select(Server).where(Server.invite_code == "MEETUP01"))
        if meetup_exists is None:
            meetup = Server(name="첫모임 라운지", invite_code="MEETUP01", created_by=users["sujin"].id)
            db.add(meetup)
            await db.flush()
            meetup_general = Channel(server_id=meetup.id, name="일반")
            meetup_interest = Channel(server_id=meetup.id, name="관심사 수다")
            db.add_all([meetup_general, meetup_interest])
            await db.flush()

            for key in USERS:
                await ensure_member(db, meetup.id, users[key].id)
                await ensure_tags(db, meetup.id, users[key].id, MEETUP_TAGS[key])

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
        print(f"시드 완료: 유저 {len(users)}명, 서버 2개(+데모 서버 보강), 메시지 {total}개")
        print("모든 목업 계정 비밀번호: test1234 (예: sujin@test.com)")


if __name__ == "__main__":
    asyncio.run(main())
