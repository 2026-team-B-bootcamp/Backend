#!/bin/sh
# EC2 최초 1회 셋업. Ubuntu 22.04 LTS 기준.
#
#   ssh -i <키>.pem ubuntu@<EC2-IP>
#   curl -fsSL https://raw.githubusercontent.com/2026-team-B-bootcamp/Backend/release/scripts/ec2-bootstrap.sh | sh
#   (레포가 private이면 파일을 scp로 올리거나 내용을 붙여넣어 실행한다)
#
# 실행 후 반드시 재로그인해야 docker 그룹 권한이 적용된다.
set -eu

echo "== 1/4 패키지 업데이트 =="
sudo apt-get update -y
sudo apt-get upgrade -y

echo "== 2/4 스왑 4GB =="
# t3.small은 2GB뿐이라 이미지 pull·컨테이너 기동 시 메모리 스파이크에 취약하다.
if [ ! -f /swapfile ]; then
	sudo fallocate -l 4G /swapfile
	sudo chmod 600 /swapfile
	sudo mkswap /swapfile
	sudo swapon /swapfile
	echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
else
	echo "이미 존재 — 건너뜀"
fi

echo "== 3/4 Docker =="
if ! command -v docker >/dev/null 2>&1; then
	curl -fsSL https://get.docker.com | sudo sh
	sudo usermod -aG docker ubuntu
else
	echo "이미 설치됨 — 건너뜀"
fi

# 컨테이너 로그가 디스크를 채우지 않게 로테이션을 건다(20GB EBS 보호).
sudo mkdir -p /etc/docker
echo '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' \
	| sudo tee /etc/docker/daemon.json
sudo systemctl restart docker

echo "== 4/4 배포 디렉터리 =="
sudo mkdir -p /opt/ieum
sudo chown ubuntu:ubuntu /opt/ieum

echo
echo "완료. 'exit' 후 다시 SSH 접속해야 docker 명령이 sudo 없이 동작한다."
echo "다음 단계는 DEPLOY.md 5절(최초 수동 배포)을 따른다."
