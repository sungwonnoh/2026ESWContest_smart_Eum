#!/bin/sh
# 시연 전 상태 점검 (RPi5, POSIX sh). 바꾸는 것은 없다 — 읽기만 한다.
#
# MQTT 는 브로커 컨테이너 안의 CLI, JSON 해석은 호스트 python3 → 없으면 Backend
# 컨테이너의 python 을 쓴다. 모델이 없거나 봉우리가 없으면 1 로 끝난다.
set -u

MQTT_CONTAINER=${MQTT_CONTAINER:-mosquitto}
ENGINE_NAME=${ENGINE_NAME:-hestia-engine}
BACKEND_NAME=${BACKEND_NAME:-hestia-api}
API=${API:-http://127.0.0.1:8000/api/v1}

FAIL=0
ok() { echo "  ✅ $*"; }
ng() { echo "  ❌ $*"; FAIL=1; }
warn() { echo "  ⚠️  $*"; }

msub() { docker exec "$MQTT_CONTAINER" mosquitto_sub -h localhost "$@"; }
running() { docker ps --format '{{.Names}}' | grep -qx "$1"; }

retained_one() {
    if out=$(msub -t "$1" -C 1 -W 2 2>/dev/null); then printf '%s' "$out"; fi
}

# 표준 입력의 JSON 을 python 코드($1)로 읽는다
pyjson() {
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "$1"
    elif running "$BACKEND_NAME"; then
        docker exec -i "$BACKEND_NAME" python -c "$1"
    else
        echo "  (python 이 없어 해석을 건너뜀)"
        cat >/dev/null
    fi
}

http_code() {
    if command -v curl >/dev/null 2>&1; then
        c=$(curl -s -o /dev/null -w '%{http_code}' "$1" 2>/dev/null) || true
        echo "${c:-000}"
    elif running "$BACKEND_NAME"; then
        docker exec "$BACKEND_NAME" python -c '
import sys, urllib.request, urllib.error
try:
    print(urllib.request.urlopen(sys.argv[1], timeout=5).status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print("000")' "$1"
    else
        echo 000
    fi
}

MODEL_PY='
import json, sys
m = json.load(sys.stdin)
hm = lambda x: "%02d:%02d" % divmod(int(x), 60)
peaks = m["distributions"]["meal_time"].get("peaks") or []
days = m["sample_days"]
centers = ", ".join(hm(p["center"]) for p in peaks) or "없음"
mark = "✅" if len(peaks) >= 2 else "❌"
print("  %s 모델 sample_days=%s 피크=%s  (p2 이면 91일, 11:00 · 16:45)" % (mark, days, centers))
sys.exit(0 if len(peaks) >= 2 else 1)
'

MEDS_PY='
import json, sys
from datetime import datetime, timedelta, timezone
m = json.load(sys.stdin)
items = m["medications"]
at = datetime.fromtimestamp(m["sent_ts"], timezone(timedelta(hours=9))).strftime("%H:%M")
print("  복약 %d건, 등록 기준 시각 %s (이 시각 전에 끝난 오늘 식사는 건너뛴다)" % (len(items), at))
for x in items:
    s = x["schedule"]
    when = ("식후 %s분" % s["delay_min"]) if s["type"] == "after_meal" else " · ".join(s["times"])
    print("    - %s: %s, %s~%s" % (x["name"], when, x["start_date"], x["end_date"]))
'

echo "== 컨테이너"
for c in "$MQTT_CONTAINER" "$ENGINE_NAME" "$BACKEND_NAME"; do
    if running "$c"; then ok "$c 실행 중"; else ng "$c 가 실행 중이 아니다"; fi
done
if running "$ENGINE_NAME"; then
    cpu=$(docker stats --no-stream --format '{{.CPUPerc}}' "$ENGINE_NAME" 2>/dev/null || echo '?')
    echo "  엔진 CPU: $cpu (수십 % 이상이 계속되면 재연결 루프를 의심)"
fi

echo "== 브로커 retained"
model=$(retained_one hestia/model/kde)
if [ -z "$model" ]; then
    ng "hestia/model/kde 없음 — 식후 복약·식사 알림이 나가지 않는다"
elif ! printf '%s' "$model" | pyjson "$MODEL_PY"; then
    FAIL=1
fi

meds=$(retained_one hestia/registry/medications)
if [ -z "$meds" ]; then
    warn "hestia/registry/medications 없음 — Backend 가 MQTT 에 아직 붙지 않았다"
else
    printf '%s' "$meds" | pyjson "$MEDS_PY" || warn "복약 목록 해석 실패"
fi

if [ -n "$(retained_one hestia/sim/clock)" ]; then ng "hestia/sim/clock 이 남아 있다"; else ok "hestia/sim/clock 없음"; fi
profile=$(retained_one hestia/system/profile)
if [ -n "$profile" ]; then echo "  프로파일: $profile"; fi

echo "== Backend API ($API)"
for path in setup medications weather; do
    code=$(http_code "$API/$path")
    case $code in
        200) ok "GET /$path" ;;
        404) warn "GET /$path 404 (setup: 최초 설정 전 / weather: 아직 날씨를 못 받음)" ;;
        *)   ng "GET /$path $code" ;;
    esac
done

echo "== 시각"
echo "  RPi5 지금: $(date '+%F %T %Z')"

exit "$FAIL"
