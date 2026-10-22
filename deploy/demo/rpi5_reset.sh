#!/usr/bin/env bash
# 시연 초기화 (RPi5). 브로커 retained · Backend DB · 엔진 t0 로그를 비운다.
#
#   1) 엔진·Backend 를 멈춘다 (systemd 유닛이나 docker 컨테이너 중 있는 쪽)
#   2) hestia/# retained 를 전부 지운다 — sim/clock 포함
#   3) Backend DB 와 엔진 t0 로그를 백업 폴더로 옮긴다 (지우지 않는다)
#
# 서비스는 다시 켜지 않는다. RPi4 에서 p2 모델을 발행한 뒤 rpi5_start.sh 로 켠다.
# 경로·이름이 이 기기와 다르면 환경변수로 덮어쓴다.
#   BACKEND_DB=/data/hestia.db BACKEND_NAME=hestia-api ./rpi5_reset.sh
set -euo pipefail

BROKER=${BROKER:-localhost}
ENGINE_NAME=${ENGINE_NAME:-hestia-engine}
BACKEND_NAME=${BACKEND_NAME:-hestia-api}
BACKEND_DB=${BACKEND_DB:-/data/hestia/backend/hestia.db}
ENGINE_T0=${ENGINE_T0:-/data/hestia/t0_log.jsonl}
BACKUP=${BACKUP:-/data/hestia/backup/$(date +%Y%m%d-%H%M%S)}

stop() {
  local name=$1
  if systemctl cat "$name" >/dev/null 2>&1; then
    sudo systemctl stop "$name" && echo "  systemd 정지: $name"
  elif command -v docker >/dev/null && docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
    docker stop "$name" >/dev/null && echo "  docker 정지: $name"
  else
    echo "  없음(건너뜀): $name"
  fi
}

retained_topics() {
  # -W 가 끝나면 종료 코드 27 이 난다 — 정상
  mosquitto_sub -h "$BROKER" -t 'hestia/#' -v --retained-only -W 3 2>/dev/null \
    | awk '{print $1}' | sort -u || true
}

echo "[1/3] 서비스 정지"
stop "$ENGINE_NAME"
stop "$BACKEND_NAME"

echo "[2/3] retained 삭제 ($BROKER)"
topics=$(retained_topics)
if [ -z "$topics" ]; then
  echo "  남은 retained 없음"
else
  echo "$topics" | while read -r t; do
    mosquitto_pub -h "$BROKER" -t "$t" -r -n -q 1
    echo "  삭제: $t"
  done
fi
left=$(retained_topics | wc -l | tr -d ' ')
echo "  남은 retained: $left 개 (ESP32·Tasmota 가 켜져 있으면 곧 현재 상태를 다시 올린다)"

echo "[3/3] DB·로그 백업 → $BACKUP"
sudo mkdir -p "$BACKUP"
for f in "$BACKEND_DB" "$BACKEND_DB-wal" "$BACKEND_DB-shm" "$ENGINE_T0"; do
  if [ -e "$f" ]; then
    sudo mv "$f" "$BACKUP/" && echo "  이동: $f"
  fi
done

echo
echo "완료. 다음: RPi4 에서 rpi4_prepare.sh → RPi5 에서 rpi5_start.sh"
