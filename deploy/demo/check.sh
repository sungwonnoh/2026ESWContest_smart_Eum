#!/usr/bin/env bash
# 시연 전 상태 점검 (RPi5). 바꾸는 것은 없다 — 읽기만 한다.
set -uo pipefail

BROKER=${BROKER:-localhost}
API=${API:-http://127.0.0.1:8000/api/v1}

retained() { mosquitto_sub -h "$BROKER" -t "$1" -C 1 -W 2 2>/dev/null; }
ok() { echo "  ✅ $*"; }
ng() { echo "  ❌ $*"; }

echo "== 브로커 retained"
model=$(retained hestia/model/kde)
if [ -z "$model" ]; then
  ng "hestia/model/kde 없음 — 식후 복약·식사 알림이 나가지 않는다"
else
  echo "$model" | python3 -c '
import json, sys
m = json.load(sys.stdin)
hm = lambda x: "%02d:%02d" % divmod(int(x), 60)
peaks = m["distributions"]["meal_time"].get("peaks") or []
days = m["sample_days"]
mark = "✅" if days == 91 and len(peaks) == 2 else "❌"
centers = ", ".join(hm(p["center"]) for p in peaks) or "없음"
print("  %s 모델 sample_days=%s 피크=%s  (p2 이면 91일, 11:00·16:45)" % (mark, days, centers))
'
fi

meds=$(retained hestia/registry/medications)
if [ -z "$meds" ]; then
  ng "hestia/registry/medications 없음 — Backend 가 MQTT 에 붙지 않았다"
else
  echo "$meds" | python3 -c '
import json, sys
m = json.load(sys.stdin)
import datetime as d
items = m["medications"]
at = d.datetime.fromtimestamp(m["sent_ts"], d.timezone(d.timedelta(hours=9))).strftime("%H:%M")
print("  복약 %d건, 등록 기준 시각 %s (이 시각 전에 끝난 오늘 식사는 건너뛴다)" % (len(items), at))
for x in items:
    s = x["schedule"]
    if s["type"] == "after_meal":
        when = "식후 %s분" % s["delay_min"]
    else:
        when = " · ".join(s["times"])
    print("    - %s: %s, %s~%s" % (x["name"], when, x["start_date"], x["end_date"]))
'
fi

if [ -n "$(retained hestia/sim/clock)" ]; then ng "hestia/sim/clock 이 남아 있다"; else ok "hestia/sim/clock 없음"; fi
profile=$(retained hestia/system/profile)
[ -n "$profile" ] && echo "  프로파일: $profile"

echo "== Backend API ($API)"
for path in setup medications weather; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$API/$path")
  case "$code" in
    200) ok "GET /$path" ;;
    404) echo "  ⚠️  GET /$path 404 (setup: 최초 설정 전, weather: 아직 날씨를 못 받음)" ;;
    *)   ng "GET /$path $code" ;;
  esac
done

echo "== 시각"
echo "  RPi5 지금: $(date '+%F %T %Z')"
