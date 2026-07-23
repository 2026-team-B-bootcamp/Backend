#!/bin/sh
# EC2 최초 셋업. Ubuntu LTS(22.04~26.04) 기준.
#
#   curl -fsSL https://raw.githubusercontent.com/2026-team-B-bootcamp/Backend/release/scripts/ec2-bootstrap.sh -o /tmp/bootstrap.sh
#   sh /tmp/bootstrap.sh
#
# 파일로 받아서 실행하는 이유: `curl | sh`는 중간에 실패해도 어디서 죽었는지
# 알기 어렵고, 다시 돌리려면 매번 다시 받아야 한다.
#
# 몇 번을 다시 돌려도 안전하다(멱등). 앞선 실행이 중간에 끊겼다면 이어서 마무리한다.
# 실행 후 반드시 재로그인해야 docker 그룹 권한이 적용된다.
set -eu

# apt가 설정 파일 충돌·서비스 재시작 여부를 물어보는 전체화면 대화상자를 띄우면
# 스크립트가 거기서 멈춘다. 비대화 모드로 고정하고, 설정 파일은 기존 것을 유지한다.
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
APT_OPTS='-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold'

echo "== 1/5 중단된 패키지 설치 복구 =="
# 앞선 apt 실행이 (대화상자·Ctrl+C·재부팅 등으로) 끊겼으면 dpkg가 잠긴 상태로 남고
# 이후 모든 apt 명령이 실패한다. 잠겨 있지 않으면 이 명령은 아무 일도 하지 않는다.
sudo dpkg --configure -a

echo "== 2/5 패키지 업데이트 =="
sudo apt-get update -y
# shellcheck disable=SC2086
sudo apt-get upgrade $APT_OPTS

echo "== 3/5 스왑 4GB =="
# t3.small은 2GB뿐이라 이미지 pull·컨테이너 기동 시 메모리 스파이크에 취약하다.
if [ ! -f /swapfile ]; then
	sudo fallocate -l 4G /swapfile
	sudo chmod 600 /swapfile
	sudo mkswap /swapfile
	sudo swapon /swapfile
	echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
else
	echo "/swapfile 이미 존재 — 생성 건너뜀"
	# 파일은 있는데 활성화가 안 된 상태(앞선 실행이 mkswap 직후 끊긴 경우)도 살린다.
	sudo swapon /swapfile 2>/dev/null || true
fi
swapon --show

echo "== 4/5 Docker =="
if ! command -v docker >/dev/null 2>&1; then
	curl -fsSL https://get.docker.com | sudo sh
else
	echo "docker 이미 설치됨 — 설치 건너뜀"
fi

# docker 그룹은 보통 docker-ce 패키지의 postinst가 만든다. 설치가 중간에 끊기면
# 바이너리는 있는데 그룹은 없는 상태로 남고, usermod가
# "group 'docker' does not exist"로 실패한다. 없으면 직접 만든다.
if ! getent group docker >/dev/null; then
	sudo groupadd docker
	echo "docker 그룹 생성"
fi

# usermod는 설치 여부와 무관하게 매번 확인한다. 설치 블록 안에 두면, 앞선 실행이
# 도커 설치 직후에 끊겼을 때 그룹 추가가 영영 안 되고 사용자는 계속 권한 오류를 본다.
if id -nG ubuntu | tr ' ' '\n' | grep -qx docker; then
	echo "ubuntu 사용자 docker 그룹 이미 소속"
else
	sudo usermod -aG docker ubuntu
	echo "ubuntu 사용자를 docker 그룹에 추가 — 재로그인 필요"
fi

# 컨테이너 로그가 디스크를 채우지 않게 로테이션을 건다(20GB EBS 보호).
sudo mkdir -p /etc/docker
echo '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' \
	| sudo tee /etc/docker/daemon.json >/dev/null
sudo systemctl enable --now docker
sudo systemctl restart docker

echo "== 5/5 배포 디렉터리 =="
sudo mkdir -p /opt/ieum
sudo chown ubuntu:ubuntu /opt/ieum

echo
echo "─────────────────────────────────────────────"
echo "완료."
echo "  docker : $(docker --version 2>/dev/null || echo '확인 실패')"
echo "  스왑   : $(swapon --show=SIZE --noheadings 2>/dev/null || echo '없음')"
echo "  디렉터리: $(ls -d /opt/ieum)"
echo
echo "이제 'exit' 후 다시 SSH로 접속해야 docker 명령이 sudo 없이 동작한다."
echo "다음 단계는 DEPLOY.md 5절(최초 수동 배포)."
echo "─────────────────────────────────────────────"
