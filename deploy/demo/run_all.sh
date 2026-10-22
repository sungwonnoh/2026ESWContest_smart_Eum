#!/bin/sh
# WSL 에서 시연 준비를 순서대로 실행한다 (POSIX sh).
#
#   RPi5 rpi5_reset.sh → RPi4 rpi4_prepare.sh → RPi5 rpi5_start.sh
#   → (앱 최초 설정·복약 등록 — 터미널이면 Enter 를 기다린다) → RPi5 check.sh
#
# 스크립트는 ssh 표준 입력으로 보낸다 (원격은 /bin/sh). 하나라도 실패하면 멈춘다.
# 아래 변수가 로컬에 있으면 원격 스크립트 앞에 그대로 붙여 넘긴다.
#   DRY_RUN=1 ./run_all.sh                    계획만 출력
#   ENGINE_NAME=ce BACKEND_NAME=api ./run_all.sh
set -eu

RPI5=${RPI5:-root@172.20.10.4}
RPI4=${RPI4:-root@172.20.10.2}
BROKER=${BROKER:-${RPI5#*@}}
SKIP_PAUSE=${SKIP_PAUSE:-0}

DIR=$(cd "$(dirname "$0")" && pwd)
SSH_OPTS="-T -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -o LogLevel=ERROR"

# 원격으로 넘길 변수 (로컬에 설정돼 있을 때만)
PASS="DRY_RUN MQTT_CONTAINER ENGINE_NAME BACKEND_NAME BACKEND_DB ENGINE_T0 BACKUP
      CONNECT_WAIT STRICT_ENGINE_FIX APP_DIR P2_LOG IMAGE PORT API
      LEARNING_UNITS LEARNING_CONTAINERS"

env_lines() {
    for name in $PASS; do
        eval "isset=\${$name+x}"
        if [ -n "$isset" ]; then
            eval "val=\$$name"
            # 작은따옴표로 감싸고, 값 안의 ' 는 '\'' 로 바꾼다
            printf "%s='%s'\n" "$name" "$(printf '%s' "$val" | sed "s/'/'\\\\''/g")"
        fi
    done
}

remote() {
    host=$1
    script=$2
    shift 2
    [ -f "$DIR/$script" ] || { echo "❌ 없는 스크립트: $DIR/$script" >&2; exit 1; }
    echo
    echo "=================== $host · $script"
    {
        env_lines
        for kv in "$@"; do printf '%s\n' "$kv"; done
        cat "$DIR/$script"
    } | ssh $SSH_OPTS "$host" sh -s
}

remote "$RPI5" rpi5_reset.sh
remote "$RPI4" rpi4_prepare.sh "BROKER='$BROKER'"
remote "$RPI5" rpi5_start.sh

if [ "${DRY_RUN:-0}" != "1" ] && [ "$SKIP_PAUSE" != "1" ] && [ -t 0 ]; then
    echo
    printf '앱에서 최초 설정과 복약 등록을 마친 뒤 Enter (건너뛰려면 그냥 Enter) '
    read -r _
fi

if [ "${DRY_RUN:-0}" = "1" ]; then
    remote "$RPI5" check.sh || echo "(DRY_RUN — 점검 실패는 무시한다)"
else
    remote "$RPI5" check.sh
fi
echo
echo "✅ 시연 준비 끝"
