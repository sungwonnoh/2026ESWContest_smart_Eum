#!/bin/sh
# 시연 초기화 (RPi5, POSIX sh). 브로커 retained · Backend DB · 엔진 t0 로그를 비운다.
#
#   0) 계획 출력 — 멈출 서비스, 지울 retained, 옮길 파일, 백업 경로
#   1) 엔진·Backend 정지 (docker 컨테이너, 없으면 systemd 유닛)
#   2) hestia/# retained 전부 삭제 — sim/clock 포함
#   3) Backend DB 와 엔진 t0 로그를 백업 폴더로 이동 (지우지 않는다)
#
# MQTT CLI 는 브로커 컨테이너(mosquitto) 안의 것을 쓴다. 호스트에 없어도 된다.
# 어느 단계든 실패하면 거기서 멈춘다. DRY_RUN=1 이면 계획만 출력한다.
#
#   ssh root@172.20.10.4 sh -s < rpi5_reset.sh
#   (변수는 스크립트 앞에 줄로 붙인다 — run_all.sh 가 그렇게 한다)
set -eu

MQTT_CONTAINER=${MQTT_CONTAINER:-mosquitto}
ENGINE_NAME=${ENGINE_NAME:-hestia-engine}
BACKEND_NAME=${BACKEND_NAME:-hestia-api}
BACKEND_DB=${BACKEND_DB:-/data/hestia/backend/hestia.db}
ENGINE_T0=${ENGINE_T0:-/data/hestia/t0_log.jsonl}
BACKUP=${BACKUP:-/data/hestia/backup/$(date +%Y%m%d-%H%M%S)}
DRY_RUN=${DRY_RUN:-0}

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

die() { echo "  ❌ $*" >&2; exit 1; }

msub() { docker exec "$MQTT_CONTAINER" mosquitto_sub -h localhost "$@"; }
mpub() { docker exec "$MQTT_CONTAINER" mosquitto_pub -h localhost "$@"; }

has_container() { docker ps -a --format '{{.Names}}' | grep -qx "$1"; }
has_unit() { command -v systemctl >/dev/null 2>&1 && systemctl cat "$1" >/dev/null 2>&1; }

broker_ready() {
    # 27 = -W 시간 초과. 연결은 됐고 메시지가 없었다는 뜻이다
    if msub -t '$SYS/broker/uptime' -C 1 -W 3 >/dev/null 2>&1; then rc=0; else rc=$?; fi
    [ "$rc" -eq 0 ] || [ "$rc" -eq 27 ]
}

retained_topics() {
    # 페이로드가 여러 줄이어도 토픽 줄만 남긴다
    if out=$(msub -t 'hestia/#' -v --retained-only -W 3 2>/dev/null); then rc=0; else rc=$?; fi
    case $rc in
        0|27) printf '%s\n' "$out" | awk '$1 ~ /^hestia\// {print $1}' | sort -u ;;
        *)    return 1 ;;
    esac
}

stop_service() {
    if has_container "$1"; then
        docker stop "$1" >/dev/null || die "docker stop $1 실패"
        echo "  docker 정지: $1"
    elif has_unit "$1"; then
        $SUDO systemctl stop "$1" || die "systemctl stop $1 실패"
        echo "  systemd 정지: $1"
    else
        echo "  없음(건너뜀): $1"
    fi
}

mounts() {
    has_container "$1" || return 0
    docker inspect -f '{{range .Mounts}}    {{.Source}} -> {{.Destination}}{{println}}{{end}}' "$1"
}

# ------------------------------------------------------------------ 0) 계획

command -v docker >/dev/null 2>&1 || die "docker 가 없다"
docker ps --format '{{.Names}}' | grep -qx "$MQTT_CONTAINER" \
    || die "브로커 컨테이너 '$MQTT_CONTAINER' 가 실행 중이 아니다"
broker_ready || die "브로커에 붙지 못했다 ($MQTT_CONTAINER)"

topics=$(retained_topics) || die "retained 목록을 읽지 못했다"
count=$(printf '%s\n' "$topics" | grep -c . || true)

echo "== 계획"
echo "  정지할 서비스: $ENGINE_NAME, $BACKEND_NAME"
echo "  지울 retained ($count 개):"
if [ -n "$topics" ]; then printf '%s\n' "$topics" | sed 's/^/    /'; fi
echo "  옮길 파일 → $BACKUP"
moves=""
for f in "$BACKEND_DB" "$BACKEND_DB-wal" "$BACKEND_DB-shm" "$ENGINE_T0"; do
    if [ -e "$f" ]; then
        echo "    있음  $f"
        moves="$moves $f"
    else
        echo "    없음  $f"
    fi
done
if [ ! -e "$BACKEND_DB" ]; then
    echo "  ⚠️  Backend DB 가 위 경로에 없다. 컨테이너 마운트를 보고 BACKEND_DB 를 맞출 것:"
    mounts "$BACKEND_NAME"
fi
if [ ! -e "$ENGINE_T0" ]; then
    echo "  ⚠️  엔진 t0 로그가 위 경로에 없다. 엔진 마운트:"
    mounts "$ENGINE_NAME"
fi

if [ "$DRY_RUN" = "1" ]; then
    echo "DRY_RUN=1 — 아무것도 바꾸지 않았다"
    exit 0
fi

# ------------------------------------------------------------------ 1) 정지

echo "[1/3] 서비스 정지"
stop_service "$ENGINE_NAME"
stop_service "$BACKEND_NAME"

# ------------------------------------------------------------------ 2) retained

echo "[2/3] retained 삭제"
if [ -n "$topics" ]; then
    printf '%s\n' "$topics" | while IFS= read -r t; do
        mpub -t "$t" -r -n -q 1 || die "삭제 실패: $t"
        echo "  삭제: $t"
    done
fi
left=$(retained_topics) || die "삭제 후 retained 를 읽지 못했다"
if [ -n "$left" ]; then
    echo "  남은 retained (ESP32·Tasmota 가 켜져 있으면 현재 상태를 곧 다시 올린다):"
    printf '%s\n' "$left" | sed 's/^/    /'
    if printf '%s\n' "$left" | grep -qxE 'hestia/sim/clock|hestia/model/kde|hestia/registry/medications'; then
        die "지운 뒤에도 sim/clock · model · registry 가 남았다 — 다른 발행자가 있다"
    fi
else
    echo "  남은 retained 없음"
fi

# ------------------------------------------------------------------ 3) 백업

echo "[3/3] DB·로그 백업 → $BACKUP"
if [ -n "$moves" ]; then
    $SUDO mkdir -p "$BACKUP"
    for f in $moves; do
        $SUDO mv "$f" "$BACKUP/" || die "이동 실패: $f"
        echo "  이동: $f"
    done
else
    echo "  옮길 파일 없음"
fi

echo
echo "완료. 다음: RPi4 rpi4_prepare.sh → RPi5 rpi5_start.sh"
