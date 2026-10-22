#!/bin/sh
# 시연 시작 (RPi5, POSIX sh). p2 모델 확인 → 엔진 → Backend 순으로 켠다.
#
# 엔진을 먼저 켠다 — 구독하자마자 retained 모델을 받아야 끼니 구간이 생긴다.
# Backend 는 켜질 때 복약 목록(지금은 빈 목록)을 retained 로 다시 보낸다.
#
# 엔진 재연결 버그: 기동 때 브로커에 못 붙으면 다시 붙지 않고 CPU 100% 로 돈다
# (runner.run 이 loop() 반환값을 보지 않고 reconnect 하지 않는다). 그래서
#   1) 이미지에 reconnect 가 있는지 본다. 없으면 경고 (STRICT_ENGINE_FIX=1 이면 중단)
#   2) 브로커가 연결을 받는 것을 확인한 뒤에만 엔진을 켠다
#   3) CONNECT_WAIT 초 안에 "연결됨" 로그가 없으면 엔진을 다시 멈추고 중단한다
set -eu

MQTT_CONTAINER=${MQTT_CONTAINER:-mosquitto}
ENGINE_NAME=${ENGINE_NAME:-hestia-engine}
BACKEND_NAME=${BACKEND_NAME:-hestia-api}
CONNECT_WAIT=${CONNECT_WAIT:-20}
STRICT_ENGINE_FIX=${STRICT_ENGINE_FIX:-0}
DRY_RUN=${DRY_RUN:-0}

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

die() { echo "  ❌ $*" >&2; exit 1; }
# DRY_RUN 에서는 앞 단계가 아무것도 안 했으므로 멈추지 않고 알리기만 한다
need() { if [ "$DRY_RUN" = "1" ]; then echo "  ⚠️  (DRY_RUN) $*"; else die "$*"; fi; }

msub() { docker exec "$MQTT_CONTAINER" mosquitto_sub -h localhost "$@"; }
mpub() { docker exec "$MQTT_CONTAINER" mosquitto_pub -h localhost "$@"; }

has_container() { docker ps -a --format '{{.Names}}' | grep -qx "$1"; }
has_unit() { command -v systemctl >/dev/null 2>&1 && systemctl cat "$1" >/dev/null 2>&1; }

broker_ready() {
    if msub -t '$SYS/broker/uptime' -C 1 -W 3 >/dev/null 2>&1; then rc=0; else rc=$?; fi
    [ "$rc" -eq 0 ] || [ "$rc" -eq 27 ]
}

retained_one() {
    # 있으면 페이로드를, 없으면 빈 문자열을 낸다 (27 = 시간 초과 = 없음)
    if out=$(msub -t "$1" -C 1 -W 2 2>/dev/null); then rc=0; else rc=$?; fi
    case $rc in
        0)  printf '%s' "$out" ;;
        27) ;;                          # 없음
        *)  return 1 ;;
    esac
}

# 엔진 이미지에 재연결 수정이 들어 있나: fixed / buggy / unknown
engine_fix_state() {
    has_container "$ENGINE_NAME" || { echo unknown; return 0; }
    img=$(docker inspect -f '{{.Config.Image}}' "$ENGINE_NAME" 2>/dev/null) || { echo unknown; return 0; }
    code='import inspect, hestia_engine.runner as r; print("fixed" if "reconnect(" in inspect.getsource(r.Runner) else "buggy")'
    for bin in python python3; do
        if state=$(docker run --rm --entrypoint "$bin" "$img" -c "$code" </dev/null 2>/dev/null); then
            echo "$state"
            return 0
        fi
    done
    echo unknown
}

start_service() {
    if has_container "$1"; then
        docker start "$1" >/dev/null || die "docker start $1 실패"
        echo "  docker 시작: $1"
    elif has_unit "$1"; then
        $SUDO systemctl start "$1" || die "systemctl start $1 실패"
        echo "  systemd 시작: $1"
    else
        die "서비스가 없다: $1 (ENGINE_NAME / BACKEND_NAME 확인)"
    fi
}

stop_service() {
    if has_container "$1"; then docker stop "$1" >/dev/null || true
    elif has_unit "$1"; then $SUDO systemctl stop "$1" || true
    fi
}

engine_logs_since() {
    if has_container "$ENGINE_NAME"; then
        docker logs --since "$1" "$ENGINE_NAME" 2>&1
    elif has_unit "$ENGINE_NAME"; then
        journalctl -u "$ENGINE_NAME" --since "@$1" --no-pager 2>/dev/null
    fi
}

# ------------------------------------------------------------------ 1) 확인

echo "[1/4] 브로커·모델 확인"
command -v docker >/dev/null 2>&1 || die "docker 가 없다"
broker_ready || die "브로커($MQTT_CONTAINER)가 연결을 받지 않는다 — 이 상태로 엔진을 켜면 CPU 100% 루프에 빠진다"
echo "  브로커 연결 됨"
model=$(retained_one hestia/model/kde) || die "모델을 읽지 못했다"
if [ -z "$model" ]; then
    need "hestia/model/kde 가 없다 — RPi4 에서 rpi4_prepare.sh 를 먼저 실행할 것"
else
    case $model in
        *'"peaks"'*) echo "  모델 있음 (peaks 포함)" ;;
        *)           need "모델에 끼니 peaks 가 없다 — 식후 복약·식사 알림이 나가지 않는다" ;;
    esac
fi

echo "[2/4] 남으면 안 되는 retained"
clock=$(retained_one hestia/sim/clock) || die "sim/clock 을 읽지 못했다"
if [ -n "$clock" ]; then
    echo "  hestia/sim/clock 이 남아 있다 ($clock) — 지운다"
    if [ "$DRY_RUN" != "1" ]; then
        mpub -t hestia/sim/clock -r -n -q 1 || die "sim/clock 삭제 실패"
    fi
else
    echo "  없음"
fi

echo "[3/4] 엔진 이미지 재연결 수정 여부"
state=$(engine_fix_state)
case $state in
    fixed)
        echo "  ✅ reconnect 있음" ;;
    buggy|unknown)
        echo "  ⚠️  재연결 수정이 없는 이미지일 수 있다 ($state)."
        echo "     브로커가 떠 있을 때만 켜고, ${CONNECT_WAIT}초 안에 연결되지 않으면 다시 멈춘다."
        echo "     실행 중 브로커를 재시작하면 엔진이 다시 붙지 않는다 — 그때는 엔진도 재시작할 것."
        if [ "$STRICT_ENGINE_FIX" = "1" ]; then
            die "STRICT_ENGINE_FIX=1 — 수정된 이미지로 바꾼 뒤 다시 실행할 것"
        fi
        ;;
esac

if [ "$DRY_RUN" = "1" ]; then
    echo "DRY_RUN=1 — 서비스를 켜지 않았다"
    exit 0
fi

# ------------------------------------------------------------------ 2) 시작

echo "[4/4] 서비스 시작"

# 이번 실행 이전에 쌓인 로그의 줄 수
before_lines=$(docker logs "$ENGINE_NAME" 2>&1 | wc -l | tr -d ' ')

start_service "$ENGINE_NAME"

i=0
connected=0

while [ "$i" -lt "$CONNECT_WAIT" ]; do
    running=$(docker inspect -f '{{.State.Running}}' "$ENGINE_NAME" 2>/dev/null || echo false)

    if [ "$running" != "true" ]; then
        die "엔진이 실행 중 종료됐다. docker logs $ENGINE_NAME 확인"
    fi

    # 이번 실행 이후 추가된 로그만 확인
    if docker logs "$ENGINE_NAME" 2>&1 |
       tail -n +"$((before_lines + 1))" |
       grep '연결됨' >/dev/null; then
        connected=1
        break
    fi

    sleep 1
    i=$((i + 1))
done

if [ "$connected" -ne 1 ]; then
    stop_service "$ENGINE_NAME"
    die "엔진이 ${CONNECT_WAIT}초 안에 브로커에 연결되지 않았다"
fi

echo "  엔진 연결 확인 (${i}초)"

start_service "$BACKEND_NAME"

echo
echo "다음: 앱에서 최초 설정 → 복약 등록 → check.sh"
