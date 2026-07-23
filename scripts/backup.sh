#!/bin/sh
# DB + 업로드 아바타 백업. EC2에서 cron으로 매일 돌린다.
#
#   설치: sudo crontab -u ubuntu -e
#         0 3 * * * /opt/ieum/scripts/backup.sh >> /opt/ieum/backups/backup.log 2>&1
#
# 단일 EC2 + 단일 EBS 구성이라 이 백업이 유일한 복구 수단이다.
# EBS 스냅샷(AWS Data Lifecycle Manager)도 함께 켜두면 인스턴스 통째 복구가 된다.
set -eu

DEPLOY_DIR=${DEPLOY_DIR:-/opt/ieum}
BACKUP_DIR="$DEPLOY_DIR/backups"
COMPOSE="docker compose -f $DEPLOY_DIR/docker-compose.prod.yml"
RETENTION_DAYS=${RETENTION_DAYS:-14}
TS=$(date +%F-%H%M)

cd "$DEPLOY_DIR"
mkdir -p "$BACKUP_DIR"

# POSTGRES_USER / POSTGRES_DB를 .env에서 읽는다. 여기서만 쓰고 export하지 않는다.
# shellcheck disable=SC1091
. "$DEPLOY_DIR/.env"

echo "[backup] $TS 시작"

# 1) Postgres 논리 백업. -T는 TTY 할당 없이 실행(cron에는 TTY가 없다).
#    파이프 중간(pg_dump)이 실패해도 잡아내려고 임시 파일에 받은 뒤 옮긴다.
$COMPOSE exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
	| gzip > "$BACKUP_DIR/db-$TS.sql.gz.tmp"
mv "$BACKUP_DIR/db-$TS.sql.gz.tmp" "$BACKUP_DIR/db-$TS.sql.gz"

# 2) 업로드 아바타 볼륨. 실제 이름은 <compose 프로젝트명>_avatars이고 프로젝트명은
#    디렉터리명에서 오므로(/opt/ieum → ieum_avatars), 추측하지 말고 실행 중인
#    api 컨테이너의 마운트에서 직접 읽는다.
VOLUME=$(docker inspect \
	-f '{{range .Mounts}}{{if eq .Destination "/app/static/avatars"}}{{.Name}}{{end}}{{end}}' \
	"$($COMPOSE ps -q api)")
if [ -z "$VOLUME" ]; then
	echo "[backup] 아바타 볼륨을 찾지 못했습니다 — api 컨테이너가 떠 있는지 확인하세요." >&2
	exit 1
fi
docker run --rm \
	-v "$VOLUME":/src:ro \
	-v "$BACKUP_DIR":/dst \
	alpine tar czf "/dst/avatars-$TS.tar.gz" -C /src .

# 3) 보관 기간이 지난 백업 정리.
find "$BACKUP_DIR" -name 'db-*.sql.gz' -mtime "+$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -name 'avatars-*.tar.gz' -mtime "+$RETENTION_DAYS" -delete

echo "[backup] 완료:"
ls -lh "$BACKUP_DIR/db-$TS.sql.gz" "$BACKUP_DIR/avatars-$TS.tar.gz"
