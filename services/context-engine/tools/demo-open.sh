#!/usr/bin/env bash
# 시연 화면. 창 여섯을 각각 별도 터미널 창으로 띄운다 (macOS Terminal.app).
#
# tmux 분할은 한 칸이 좁아 줄이 접힌다 — 창 하나당 62열 이상이 필요하다.
# 이 스크립트는 진짜 창을 여섯 개 연다.
#
#   bash tools/demo-open.sh
#   MQTT_HOST=172.20.10.3 CONFIG=../../config-test/homes/demo.toml bash tools/demo-open.sh
#
# iTerm2 를 쓰면 TERM_APP=iTerm 을 주면 된다.
#   TERM_APP=iTerm bash tools/demo-open.sh
#
# 닫을 때는 각 창에서 Ctrl-C.

set -euo pipefail

HOST="${MQTT_HOST:-localhost}"
PORT="${MQTT_PORT:-1883}"
APP="${TERM_APP:-Terminal}"
VIEWS="${VIEWS:-input activity decision action board t0}"

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"          # services/context-engine

# venv 가 있으면 켜서 넘긴다 — 새 창은 이 셸의 환경을 물려받지 않는다
ACTIVATE=""
if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  ACTIVATE="source '$ROOT/.venv/bin/activate'; "
fi

CONF_ARG=""
if [[ -n "${CONFIG:-}" ]]; then
  CONF_ARG="--config '$CONFIG'"
fi

for view in $VIEWS; do
  cmd="cd '$ROOT'; ${ACTIVATE}python tools/monitor.py --host '$HOST' --port '$PORT' --view $view $CONF_ARG"
  osascript -e "tell application \"$APP\" to do script \"$cmd\"" >/dev/null
done

osascript -e "tell application \"$APP\" to activate" >/dev/null
echo "창 $(echo $VIEWS | wc -w | tr -d ' ')개를 열었다 — $APP"
echo "각 창을 62열 이상으로 넓히세요. 닫을 때는 창마다 Ctrl-C."