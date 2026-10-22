#!/usr/bin/env bash
# 시연 시작 (RPi5). p2 모델이 브로커에 있는지 확인한 뒤 엔진 → Backend 순으로 켠다.
#
# 엔진을 먼저 켠다 — 구독하자마자 retained 모델을 받아야 끼니 구간이 생긴다.
# Backend 는 켜질 때 복약 목록(지금은 빈 목록)을 retained 로 다시 보낸다.
set -euo pipefail

BROKER=${BROKER:-localhost}
ENGINE_NAME=${ENGINE_NAME:-hestia-engine}
BACKEND_NAME=${BACKEND_NAME:-hestia-api}

start() {
  local name=$1
  if systemctl cat "$name" >/dev/null 2>&1; then
    sudo systemctl start "$name" && echo "  systemd 시작: $name"
  elif command -v docker >/dev/null && docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
    docker start "$name" >/dev/null && echo "  docker 시작: $name"
  else
    echo "  없음 — 직접 켤 것: $name"
  fi
}

echo "[1/3] p2 모델 확인"
if ! mosquitto_sub -h "$BROKER" -t hestia/model/kde -C 1 -W 3 >/dev/null 2>&1; then
  echo "  hestia/model/kde 가 없다 — RPi4 에서 rpi4_prepare.sh 를 먼저 실행할 것"
  exit 1
fi
echo "  있음"

echo "[2/3] 남으면 안 되는 retained"
if mosquitto_sub -h "$BROKER" -t hestia/sim/clock -C 1 -W 2 >/dev/null 2>&1; then
  echo "  hestia/sim/clock 이 남아 있다 — 엔진 시각이 지난 시연으로 넘어간다. 지운다"
  mosquitto_pub -h "$BROKER" -t hestia/sim/clock -r -n -q 1
fi
echo "  확인 끝"

echo "[3/3] 서비스 시작"
start "$ENGINE_NAME"
sleep 3
start "$BACKEND_NAME"

echo
echo "다음: 앱에서 최초 설정 → 복약 등록 → check.sh"
