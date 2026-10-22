#!/bin/sh
# 시연 준비 (RPi4, POSIX sh). 실측을 끄고 p2 모델을 한 번 발행한다.
#
#   0) 계획 출력
#   1) 실데이터 학습·t0 수집을 끈다 — 켜 두면 실데이터 모델이 p2 모델을 덮어쓰고
#      시연 이벤트가 실데이터 로그에 쌓인다
#   2) p2 로그 확인 (학습 이미지의 python 으로 읽는다 — 호스트에 python 이 없어도 된다)
#   3) p2 모델 발행 → hestia/model/kde (retained)
#   4) 브로커에서 다시 받아 끼니 봉우리를 확인한다 (학습 이미지의 paho 로 구독)
#
# 실패하면 거기서 멈춘다. DRY_RUN=1 이면 계획만 출력한다.
# 시연이 끝나면 꺼 둔 것을 다시 켠다 (출력되는 안내 참고).
set -eu

BROKER=${BROKER:-172.20.10.4}
PORT=${PORT:-1883}
APP_DIR=${APP_DIR:-/data/hestia-learning}
P2_LOG=${P2_LOG:-data/synthetic/p1_p2/p2_t0_log.jsonl}
IMAGE=${IMAGE:-hestia-learning:latest}
LEARNING_UNITS=${LEARNING_UNITS:-hestia-learning.timer hestia-t0-logger}
LEARNING_CONTAINERS=${LEARNING_CONTAINERS:-hestia-t0-logger hestia-learning}
DRY_RUN=${DRY_RUN:-0}

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

die() { echo "  ❌ $*" >&2; exit 1; }

has_unit() { command -v systemctl >/dev/null 2>&1 && systemctl cat "$1" >/dev/null 2>&1; }
running_container() { docker ps --format '{{.Names}}' | grep -qx "$1"; }

# 학습 이미지 안의 python. 코드·데이터는 APP_DIR 을 /app 으로 붙인다
py() {
    docker run --rm -i --network host --security-opt apparmor=unconfined \
        -v "$APP_DIR":/app -w /app "$IMAGE" python "$@"
}

# ------------------------------------------------------------------ 0) 계획

command -v docker >/dev/null 2>&1 || die "docker 가 없다"
docker image inspect "$IMAGE" >/dev/null 2>&1 || die "이미지 $IMAGE 가 없다"
[ -f "$APP_DIR/$P2_LOG" ] || die "p2 로그가 없다: $APP_DIR/$P2_LOG (저장소 data/synthetic/p1_p2 를 복사할 것)"
[ -f "$APP_DIR/run_learning.py" ] || die "학습 코드가 없다: $APP_DIR/run_learning.py"

echo "== 계획"
echo "  브로커: $BROKER:$PORT"
echo "  끌 것:"
stops=""
for u in $LEARNING_UNITS; do
    if has_unit "$u"; then echo "    systemd  $u"; stops="$stops unit:$u"; fi
done
for c in $LEARNING_CONTAINERS; do
    if running_container "$c"; then echo "    docker   $c"; stops="$stops ctr:$c"; fi
done
if [ -z "$stops" ]; then echo "    (실행 중인 실측 서비스 없음)"; fi
echo "  발행할 모델: $APP_DIR/$P2_LOG ($IMAGE)"

if [ "$DRY_RUN" = "1" ]; then
    echo "DRY_RUN=1 — 아무것도 바꾸지 않았다"
    exit 0
fi

# ------------------------------------------------------------------ 1) 실측 끄기

echo "[1/4] 실측 끄기"
for s in $stops; do
    case $s in
        unit:*) u=${s#unit:}; $SUDO systemctl disable --now "$u" || die "systemctl disable $u 실패"; echo "  꺼짐: $u" ;;
        ctr:*)  c=${s#ctr:};  docker stop "$c" >/dev/null || die "docker stop $c 실패"; echo "  정지: $c" ;;
    esac
done
if [ -z "$stops" ]; then echo "  끌 것 없음"; fi

# ------------------------------------------------------------------ 2) p2 로그

echo "[2/4] p2 로그 확인"
py - "$P2_LOG" <<'PY' || die "p2 로그를 읽지 못했다"
import json, sys
from datetime import datetime, timedelta, timezone
kst = timezone(timedelta(hours=9))
meals = []
with open(sys.argv[1], encoding="utf-8") as f:
    for line in f:
        r = json.loads(line)
        if r.get("type") == "meal":
            t = datetime.fromtimestamp(r["t0"], kst)
            meals.append(t.hour * 60 + t.minute)
lunch = [m for m in meals if m < 14 * 60]
dinner = [m for m in meals if m >= 14 * 60]
avg = lambda xs: "%02d:%02d" % divmod(sum(xs) // len(xs), 60) if xs else "-"
print("  식사 %d건  점심 평균 %s  저녁 평균 %s  (최신 p2 면 11시·17시 전후)" % (len(meals), avg(lunch), avg(dinner)))
PY

# ------------------------------------------------------------------ 3) 발행

echo "[3/4] p2 모델 발행 → $BROKER:$PORT"
# </dev/null — 이 스크립트가 ssh 표준 입력으로 들어오므로, docker -i 가 남은 스크립트를 먹지 않게 막는다
py run_learning.py --t0-jsonl "$P2_LOG" --host "$BROKER" --port "$PORT" </dev/null \
    || die "모델 발행 실패 (브로커 주소·포트 확인)"

# ------------------------------------------------------------------ 4) 확인

echo "[4/4] 발행된 모델 확인"
py - "$BROKER" "$PORT" <<'PY' || die "발행된 모델에 끼니 봉우리가 없거나 받지 못했다"
import json, sys, threading
import paho.mqtt.client as mqtt

host, port = sys.argv[1], int(sys.argv[2])
got = {}
done = threading.Event()

def on_message(c, u, msg):
    got["payload"] = msg.payload
    done.set()

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.on_message = on_message
c.on_connect = lambda c, u, f, rc, p: c.subscribe("hestia/model/kde", qos=1)
c.connect(host, port, 30)
c.loop_start()
ok = done.wait(5)
c.loop_stop()
if not ok or not got.get("payload"):
    sys.exit("  hestia/model/kde 를 받지 못했다")

m = json.loads(got["payload"])
hm = lambda x: "%02d:%02d" % divmod(int(x), 60)
peaks = m["distributions"]["meal_time"].get("peaks") or []
print("  sample_days %s" % m["sample_days"])
for p in peaks:
    print("  끼니 피크 %s  구간 %s ~ %s" % (hm(p["center"]), hm(p["from"]), hm(p["to"])))
if len(peaks) < 2:
    sys.exit("  끼니 봉우리가 2개 미만 — 식후 복약·식사 알림이 나가지 않는다")
PY

echo
echo "완료. 다음: RPi5 rpi5_start.sh"
echo "시연이 끝나면 다시 켤 것:"
for s in $stops; do
    case $s in
        unit:*) echo "  systemctl enable --now ${s#unit:}" ;;
        ctr:*)  echo "  docker start ${s#ctr:}" ;;
    esac
done
