# ERD - DDL (ERD Cloud 붙여넣기용)

실제 DB는 PostgreSQL 16이지만, ERD Cloud의 DDL 가져오기가 MySQL 문법을 기준으로 하므로
MySQL 호환 문법으로 변환한 DDL이다. (`SERIAL` → `INT AUTO_INCREMENT`, `timestamptz` → `TIMESTAMP`)

사용법: ERD Cloud에서 다이어그램 생성 → 우측 상단 **가져오기(Import) → DDL** → 아래 코드 블록 안의 내용 전체 붙여넣기.

> `alembic_version` 테이블은 마이그레이션 관리용이라 ERD에서 제외했다.

```sql
CREATE TABLE users (
    id            INT          NOT NULL AUTO_INCREMENT,
    email         VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    display_name  VARCHAR(100) NOT NULL,
    avatar_url    VARCHAR(500) NULL,
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY ix_users_email (email)
);

CREATE TABLE servers (
    id          INT          NOT NULL AUTO_INCREMENT,
    name        VARCHAR(100) NOT NULL,
    invite_code VARCHAR(8)   NOT NULL,
    created_by  INT          NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY ix_servers_invite_code (invite_code),
    CONSTRAINT servers_created_by_fkey FOREIGN KEY (created_by) REFERENCES users (id)
);

CREATE TABLE server_members (
    id        INT       NOT NULL AUTO_INCREMENT,
    server_id INT       NOT NULL,
    user_id   INT       NOT NULL,
    joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_member_server_user (server_id, user_id),
    CONSTRAINT server_members_server_id_fkey FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE CASCADE,
    CONSTRAINT server_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE channels (
    id         INT          NOT NULL AUTO_INCREMENT,
    name       VARCHAR(100) NOT NULL,
    server_id  INT          NOT NULL,
    created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT channels_server_id_fkey FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE CASCADE
);

CREATE TABLE messages (
    id         INT       NOT NULL AUTO_INCREMENT,
    channel_id INT       NOT NULL,
    user_id    INT       NOT NULL,
    content    TEXT      NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY ix_messages_created_at (created_at),
    CONSTRAINT messages_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES channels (id) ON DELETE CASCADE,
    CONSTRAINT messages_user_id_fkey FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE tags (
    id         INT         NOT NULL AUTO_INCREMENT,
    server_id  INT         NOT NULL,
    user_id    INT         NOT NULL,
    tag1       VARCHAR(30) NOT NULL,
    tag2       VARCHAR(30) NOT NULL,
    tag3       VARCHAR(30) NOT NULL,
    updated_at TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tag_server_user (server_id, user_id),
    CONSTRAINT tags_server_id_fkey FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE CASCADE,
    CONSTRAINT tags_user_id_fkey FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
```

## 관계 요약

- `users` 1 : N `servers` (created_by — 서버 생성자)
- `users` N : M `servers` — `server_members` 연결 테이블 (서버당 유저 1회, UNIQUE)
- `servers` 1 : N `channels`
- `channels` 1 : N `messages`, `users` 1 : N `messages`
- `tags` = 유저의 서버별 태그 3개 (server_id + user_id UNIQUE, tag1~tag3 컬럼)
