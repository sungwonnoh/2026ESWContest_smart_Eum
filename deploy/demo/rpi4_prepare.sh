#!/usr/bin/env bash
# 시연 준비 (RPi4). 실측을 끄고 p2 모델을 한 번 발행한다.
#
#   1) 실데이터 학습 타이머를 끈다 — 켜 두면 부팅 직후(Persistent)·매일 03:00 에
#      실데이터 모델이 p2 모델을 덮어쓴다
#   2) t0_logger 를 끈다 — 시연 이벤트가 실데이터 로그에 쌓이지 않게
#   3) p2 합성 로그로 학습한 모델을 hestia/model/kde 에 retained 로 발행한다
#   4) 발행된 모델의 끼니 봉우리를 확인한다
#
# 시연이 끝나면: sudo systemctl enable --now hestia-learning.timer hestia-t0-logger
set -euo pipefail

BROKER=${BROKER:-rpi5.local}       # IP 를 고정하지 않는다 (핫스팟은 다시 붙으면 바뀐다)
PORT=${PORT:-1883}
APP_DIR=${APP_DIR:-/data/hestia-learning}
P2_LOG=${P2_LOG:-data/synthetic/p1_p2/p2_t0_log.jsonl}
IMAGE=${IMAGE:-hestia-learning:latest}

echo "[1/4] 실측 끄기"
for unit in hestia-learning.timer hestia-t0-logger; do
  if systemctl cat "$unit" >/dev/null 2>&1; then
    sudo systemctl disable --now "$unit" && echo "  꺼짐: $unit"
  else
    echo "  없음(건너뜀): $unit"
  fi
done

echo "[2/4] p2 로그 확인: $APP_DIR/$P2_LOG"
test -f "$APP_DIR/$P2_LOG" || { echo "  p2 로그가 없다 — 저장소 data/synthetic/p1_p2 를 복사할 것"; exit 1; }
first_meal=$(grep -m1 '"type": "meal"' "$APP_DIR/$P2_LOG" | python3 -c 'import json,sys,datetime as d;r=json.loads(sys.stdin.read());print(d.datetime.fromtimestamp(r["t0"],d.timezone(d.timedelta(hours=9))).strftime("%H:%M"))')
echo "  첫 식사 시작 $first_meal (11시·17시 전후여야 최신 p2)"

echo "[3/4] p2 모델 발행 → $BROKER:$PORT"
docker run --rm \
  --name hestia-learning-p2 \
  --security-opt apparmor=unconfined \
  --network host \
  -v "$APP_DIR":/app -w /app \
  "$IMAGE" \
  python run_learning.py --t0-jsonl "$P2_LOG" --host "$BROKER" --port "$PORT"

echo "[4/4] 발행된 모델 확인"
mosquitto_sub -h "$BROKER" -p "$PORT" -t hestia/model/kde -C 1 -W 5 \
  | python3 -c '
import json, sys
m = json.load(sys.stdin)
hm = lambda x: "%02d:%02d" % divmod(int(x), 60)
peaks = m["distributions"]["meal_time"].get("peaks") or []
print("  sample_days", m["sample_days"])
for p in peaks:
    print("  끼니 피크", hm(p["center"]), "구간", hm(p["from"]), "~", hm(p["to"]))
if len(peaks) < 2:
    sys.exit("  끼니 봉우리가 없다 — 식후 복약·식사 알림이 나가지 않는다")
'
echo "완료. 다음: RPi5 에서 rpi5_start.sh"
