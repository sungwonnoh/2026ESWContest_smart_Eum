#!/usr/bin/env bash
# 시연 화면. 창 여섯을 한 화면에 띄운다.
#
#   ┌──────────┬──────────┬──────────┐
#   │  입력    │  판단    │  상황판  │
#   ├──────────┼──────────┼──────────┤
#   │  활동    │  개입    │  기록    │
#   └──────────┴──────────┴──────────┘
#
#   bash tools/demo-screen.sh                                 로컬 브로커
#   MQTT_HOST=172.20.10.3 bash tools/demo-screen.sh
#   MQTT_HOST=172.20.10.3 CONFIG=config/homes/demo.toml bash tools/demo-screen.sh
#
# CONFIG 를 주면 vid 가 구역·라벨(냉장고·인덕션)로 보인다.
# 끝낼 때: tmux kill-session -t hestia

set -euo pipefail

SESSION=hestia
HOST="${MQTT_HOST:-localhost}"
PORT="${MQTT_PORT:-1883}"
DIR="$(cd "$(dirname "$0")" && pwd)"

RUN=(python3 "$DIR/monitor.py" --host "$HOST" --port "$PORT")
if [[ -n "${CONFIG:-}" ]]; then
  RUN+=(--config "$CONFIG")
fi

run() { printf '%q ' "${RUN[@]}" "--view" "$1"; }

tmux kill-session -t "$SESSION" 2>/dev/null || true

# 먼저 세 칼럼
tmux new-session  -d -s "$SESSION" -n demo "$(run input)"
tmux split-window -h -t "$SESSION:demo" "$(run decision)"
tmux split-window -h -t "$SESSION:demo" "$(run board)"
tmux select-layout -t "$SESSION:demo" even-horizontal

# 칼럼마다 아래위로 쪼갠다. 인덱스는 분할하며 밀리니 pane id 로 잡는다
panes=($(tmux list-panes -t "$SESSION:demo" -F '#{pane_id}'))
tmux split-window -v -t "${panes[0]}" "$(run activity)"
tmux split-window -v -t "${panes[1]}" "$(run action)"
tmux split-window -v -t "${panes[2]}" "$(run t0)"

# 상황판은 스크롤이 없다. 틀이 다 들어갈 높이를 준다
tmux resize-pane -t "${panes[2]}" -y 24 2>/dev/null || true

tmux select-pane -t "${panes[0]}"
tmux attach -t "$SESSION"