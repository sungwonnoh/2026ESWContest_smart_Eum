#!/usr/bin/env bash
# 시연 화면. 창 일곱을 각각 별도 터미널로 띄운다.
#
# tmux 분할(demo-screen.sh)은 한 칸이 좁아 줄이 접힌다 — 창 하나당 62열
# 이상이 필요하다. 이 스크립트는 진짜 창/탭을 하나씩 연다.
#
#   bash tools/demo-open.sh
#   MQTT_HOST=172.20.10.4 CONFIG=../../config/homes/demo.toml bash tools/demo-open.sh
#   VIEWS="input board" bash tools/demo-open.sh
#
# macOS 는 Terminal.app(osascript), WSL 은 Windows Terminal(wt.exe) 을 쓴다.
# iTerm2 는 TERM_APP=iTerm.
#
# 닫을 때는 창마다 Ctrl-C.

set -euo pipefail

HOST="${MQTT_HOST:-localhost}"
PORT="${MQTT_PORT:-1883}"
APP="${TERM_APP:-Terminal}"
VIEWS="${VIEWS:-input activity decision action board t0 display}"

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"          # services/context-engine

PY=python3
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"          # 새 창은 이 셸의 환경을 안 물려받는다
fi

CONF_ARG=""
if [[ -n "${CONFIG:-}" ]]; then
  CONF_ARG="--config $CONFIG"
fi

# 창마다 돌릴 것을 임시 스크립트로 떨군다. 따옴표 중첩을 피하는 가장
# 확실한 방법이다 — wt.exe 는 ; 를 탭 구분자로 먹는다.
#
# 디렉터리를 먼저 만들고 그 안에 고정 이름으로 쓴다. BSD(macOS) mktemp 은
# X 가 템플릿 맨 끝에 와야 해서 "...-XXXXXX.sh" 가 실패한다.
WORK="$(mktemp -d "${TMPDIR:-/tmp}/hestia-demo-XXXXXX")"

make_script() {
  local view="$1" tmp="$WORK/$view.sh"
  cat > "$tmp" <<EOF
cd "$ROOT"
exec "$PY" tools/monitor.py --host "$HOST" --port "$PORT" --view $view $CONF_ARG
EOF
  chmod +x "$tmp"
  printf '%s' "$tmp"
}

if command -v wt.exe >/dev/null 2>&1; then
  # WSL — Windows Terminal 탭으로 연다
  for view in $VIEWS; do
    script="$(make_script "$view")"
    [[ -s "$script" ]] || { echo "스크립트 생성 실패: $view" >&2; exit 1; }
    wt.exe -w 0 new-tab --title "$view" wsl.exe -- bash "$script" >/dev/null 2>&1 || true
    sleep 0.3
  done
  echo "탭 $(echo "$VIEWS" | wc -w | tr -d ' ')개를 열었다 — $HOST:$PORT"
  echo "Windows Terminal 을 전체화면으로. 한 탭이 62열 이상이어야 줄이 안 접힌다."

elif command -v osascript >/dev/null 2>&1; then
  # macOS — Terminal.app 창으로 연다
  for view in $VIEWS; do
    script="$(make_script "$view")"
    [[ -s "$script" ]] || { echo "스크립트 생성 실패: $view" >&2; exit 1; }
    osascript -e "tell application \"$APP\" to do script \"bash $script\"" >/dev/null
  done
  osascript -e "tell application \"$APP\" to activate" >/dev/null
  echo "창 $(echo "$VIEWS" | wc -w | tr -d ' ')개를 열었다 — $APP"
  echo "각 창을 62열 이상으로 넓히세요."

else
  echo "창을 자동으로 못 연다 (osascript 도 wt.exe 도 없음)." >&2
  echo "탭을 직접 열고 아래를 하나씩 치세요:" >&2
  echo >&2
  for view in $VIEWS; do
    echo "  $PY tools/monitor.py --host $HOST --port $PORT --view $view $CONF_ARG" >&2
  done
  exit 1
fi