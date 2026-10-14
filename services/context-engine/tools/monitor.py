#!/usr/bin/env python3
"""시연용 모니터. 브로커를 구독해 창 하나에 한 가지만 보여준다.

    python monitor.py --host 172.20.10.3 --view input
    python monitor.py --host 172.20.10.3 --view board --config ../../config/homes/demo.toml

창 여섯:
    input     엔진이 받는 센서·가전 상태
    activity  엔진이 추론한 사용자 활동
    decision  재실·부재·인원·억제·모델
    action    판정과 개입 (알림·발화·응답·기기 명령)
    board     현재 상태 한 화면 (덮어쓰기)
    t0        학습 원재료 기록

여섯을 한 화면에 띄우려면 demo-open.sh (창 여섯 개) 또는 demo-screen.sh (tmux).

모르는 필드는 번역하지 않고 키=값 그대로 찍는다. 화면에 안 보이는 것보다
덜 다듬어져 보이는 게 낫다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("paho-mqtt 가 필요하다:  pip install paho-mqtt")


# ─── 라벨 ────────────────────────────────────────────────────────────────

AREA_KO = {
    "living": "거실", "bedroom": "침실", "kitchen": "주방",
    "bathroom": "화장실", "toilet": "화장실", "entrance": "현관",
    "utility": "다용도실",
}

SENSOR_KO = {
    "presence": "재실", "motion": "모션", "door": "문", "power": "전력",
    "bed": "침대", "light": "조도", "climate": "온습도",
}

DEVICE_KO = {
    # demo.toml 의 device_type 전부
    "smart_tv": "TV", "smart_light": "조명", "smart_fridge": "냉장고",
    "water_purifier": "정수기", "air_conditioner": "에어컨",
    "robot_cleaner": "청소기", "washer": "세탁기", "display_node": "디스플레이",
    # power_profile 로도 쓰이는 것들
    "induction": "인덕션", "microwave": "전자레인지", "kettle": "전기포트",
    "rice_cooker": "밥솥", "refrigerator": "냉장고", "water_dispenser": "정수기",
    "fan": "선풍기", "pill_dispenser": "약통", "speaker": "스피커",
}

# policy 의 [power.*] 키 → 사람이 읽는 이름
PROFILE_KO = dict(DEVICE_KO)

# activity.py 의 ACTIVITY_STATES 열 개 전부
ACTIVITY_KO = {
    "SLEEPING": "수면", "IN_BED_AWAKE": "침대 각성", "IN_SOFA_AWAKE": "소파 각성",
    "COOKING": "조리", "EATING": "식사", "KITCHEN_MISC": "주방 체류",
    "BATHROOM": "화장실", "WATCHING_TV": "TV 시청",
    "AWAY": "외출", "UNKNOWN": "불명",
}

T0_TAG = {"meal": "MEAL", "hydration": "WATER",
          "sleep_start": "SLEEP", "sleep_end": "SLEEP",
          "medication": "MEDS"}

T0_KO = {"meal": "식사", "hydration": "수분",
         "sleep_start": "수면 시작", "sleep_end": "수면 종료",
         "medication": "복약"}


def cols(s: str) -> int:
    """터미널이 먹는 칸 수. 한글·한자는 두 칸, ○ ✕ ℃ 는 한 칸(ambiguous)."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def pad(s: Any, width: int) -> str:
    """왼쪽 정렬. f-string 의 :<N 은 한글에서 어긋난다."""
    s = str(s)
    return s + " " * max(0, width - cols(s))


def area(name: Any) -> str:
    if not name:
        return "-"
    return AREA_KO.get(str(name), str(name))


def ko_bool(v: Any) -> str:
    return "○" if v else "✕"


# ─── factors 번역 ────────────────────────────────────────────────────────
# 값이 None 이면 건너뛴다. 표에 없는 키는 키=값 그대로.

def _sec(label: str) -> Callable[[Any], str]:
    return lambda v: f"{label} {int(v)}초"


def _num(label: str) -> Callable[[Any], str]:
    return lambda v: f"{label} {float(v):.2f}"


FACTOR_KO: dict[str, Callable[[Any], str]] = {
    "bed_occupied_sec": _sec("침대"),
    "sofa_occupied_sec": _sec("소파"),
    "bedroom_still_sec": _sec("침실 정지"),
    "living_still_sec": _sec("거실 정지"),
    "kitchen_still_sec": _sec("주방 정지"),
    "bathroom_still_sec": _sec("욕실 정지"),
    "meal_area_dwell_sec": _sec("식사구역 체류"),
    "cooking_off_sec": _sec("조리 종료 후"),
    "heated_sec": _sec("데움 후"),
    "tv_remote_sec": _sec("리모컨 후"),
    "cooking_on": lambda v: "조리기구 ON" if v else "조리기구 꺼짐",
    "fridge_recent": lambda v: "냉장고 최근" if v else "냉장고 안 씀",
    "sensor_fault": lambda v: "센서 고장" if v else "",
    "below_min_score": lambda v: "임계 미달" if v else "",
    "meal_time_percentile": _num("평소 대비"),
    "tv_power": lambda v: f"TV {v}",
    "asleep_area": lambda v: f"수면구역 {area(v)}",
    "hysteresis_on": lambda v: f"유지 가산 {v}",
    "held_from": lambda v: f"보류 — 원래 {v}",
    "top_score": _num("1등"),
    "score_share": _num("점유율"),
    "hour": lambda v: "",          # 타임스탬프와 중복
}


def factors_ko(factors: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    for k, v in (factors or {}).items():
        if v is None:
            continue
        fn = FACTOR_KO.get(k)
        if fn is None:
            out.append(f"{k}={v}")       # 모르는 키는 그대로
            continue
        try:
            s = fn(v)
        except Exception:
            s = f"{k}={v}"
        if s:
            out.append(s)
    return out


# ─── 인벤토리 ────────────────────────────────────────────────────────────

class Inventory:
    """vid → (구역, 라벨, 종류). config 로 시작하고 registry retained 가 오면 덮어쓴다."""

    def __init__(self) -> None:
        self._area: dict[str, str] = {}
        self._label: dict[str, str] = {}
        self._kind: dict[str, str] = {}

    def load_config(self, path: Path) -> None:
        # tomllib 은 3.11+ 다. --config 를 안 쓰면 없어도 돌아가게 늦게 들여온다
        try:
            import tomllib
        except ImportError:
            print("  tomllib 이 없다 (Python 3.11+ 필요). --config 없이 돌린다",
                  file=sys.stderr)
            return
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            print(f"  설정 읽기 실패 ({path}): {exc}", file=sys.stderr)
            return
        for block in data.get("sensors", []):
            self._put(block, device=False)
        for block in data.get("devices", []):
            self._put(block, device=True)

    def apply_registry(self, entries: list[dict]) -> None:
        for e in entries:
            if isinstance(e, dict):
                self._put(e, device=e.get("device_type") is not None)

    def _put(self, b: dict, *, device: bool) -> None:
        vid = b.get("id") or b.get("virtual_id")
        if not vid:
            return
        if b.get("area"):
            self._area[vid] = str(b["area"])
        kind = b.get("device_type") if device else b.get("type")
        if kind:
            self._kind[vid] = str(kind)
        label = b.get("label") or b.get("name") or b.get("power_profile")
        if label:
            self._label[vid] = PROFILE_KO.get(str(label), str(label))
        elif device and kind:
            self._label[vid] = DEVICE_KO.get(str(kind), str(kind))

    def area(self, vid: str) -> str:
        return area(self._area.get(vid))

    def label(self, vid: str) -> str:
        return self._label.get(vid, "")

    def kind(self, vid: str) -> str:
        return self._kind.get(vid, "")


# ─── 공통 ────────────────────────────────────────────────────────────────

def hhmmss(ts: Any) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "--:--:--"


def hhmm(ts: Any) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "--:--"


def _f2(v: Any) -> str:
    """소수 둘로. 숫자가 아니면 그대로 — 모르는 값에 죽지 않는다."""
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)


def _hm(minute: Any) -> str:
    """자정부터 분 → HH:MM."""
    try:
        m = int(float(minute)) % 1440
    except (TypeError, ValueError):
        return str(minute)
    return f"{m // 60:02d}:{m % 60:02d}"


def dur(sec: Any) -> str:
    try:
        s = int(float(sec))
    except (TypeError, ValueError):
        return "-"
    if s >= 3600:
        return f"{s // 3600}h {s % 3600 // 60:02d}m"
    if s >= 60:
        return f"{s // 60}m"
    return f"{s}s"


class View:
    """창 하나. topics() 를 구독하고 handle() 이 찍을 줄을 돌려준다."""

    title = ""
    width = 78

    def __init__(self, inv: Inventory) -> None:
        self.inv = inv

    def topics(self) -> list[str]:
        return []

    def handle(self, topic: str, p: dict) -> list[str]:
        return []

    def banner(self) -> str:
        rule = "─" * max(1, self.width - cols(self.title) - 2)
        return f" {self.title} {rule}\n"

    # 첫 줄 + 이어지는 들여쓴 줄
    def block(self, head: str, *rest: str, indent: int = 13) -> list[str]:
        out = [head]
        lead = " " * indent
        for r in rest:
            if r:
                out.append(lead + r)
        return out

    @staticmethod
    def wrap(items: list[str], limit: int = 58) -> list[str]:
        """· 으로 이어 붙이고 길면 줄을 나눈다. 한글 폭으로 잰다."""
        lines: list[str] = []
        cur = ""
        for it in items:
            cand = it if not cur else f"{cur} · {it}"
            if cols(cand) > limit and cur:
                lines.append(cur)
                cur = it
            else:
                cur = cand
        if cur:
            lines.append(cur)
        return lines


# ─── 1. 입력 ─────────────────────────────────────────────────────────────

class InputView(View):
    title = "입력"

    # 변화량이 이보다 작으면 안 찍는다. 안 그러면 조도로 화면이 덮인다
    TEMP_EPS = 0.5
    HUMID_EPS = 3.0
    LUX_EPS = 20.0

    def __init__(self, inv: Inventory) -> None:
        super().__init__(inv)
        self._last: dict[str, Any] = {}        # vid → 직전 값
        self._since: dict[str, float] = {}     # vid → 그 값이 된 시각

    def topics(self) -> list[str]:
        return ["hestia/sensor/+/state",
                "hestia/device/+/state",
                "hestia/device/+/event",
                "hestia/registry/devices"]

    def handle(self, topic: str, p: dict) -> list[str]:
        parts = topic.split("/")
        if parts[1] == "registry":
            self.inv.apply_registry(p.get("devices", []))
            return []
        vid = parts[2]
        if parts[1] == "sensor":
            return self._sensor(vid, p)
        if parts[3] == "state":
            return self._device_state(vid, p)
        return self._device_event(vid, p)

    MIN_HELD_SEC = 5          # 재실 센서가 깜빡이는 걸 매번 찍지 않는다

    # 직전 값이 얼마나 유지됐는지. 와이어에 없어서 모니터가 센다
    def _held(self, vid: str, value: Any, ts: float) -> str:
        prev, prev_at = self._last.get(vid), self._since.get(vid)
        self._last[vid] = value
        if prev != value:
            self._since[vid] = ts
        if prev is None or prev == value or prev_at is None:
            return ""
        held = int(ts - prev_at)
        if held < self.MIN_HELD_SEC:
            return ""
        return f"({self._phrase(vid, prev)} {held}초)"

    def _phrase(self, vid: str, prev: Any) -> str:
        kind = self.inv.kind(vid)
        if kind == "bed":
            return "눌림" if prev else "비움"
        if kind == "power":
            return "켜짐" if prev == "ON" else "꺼짐"
        if kind == "door":
            return "열림" if prev else "닫힘"
        if kind == "presence":
            return "있음" if prev else "없음"
        return "직전"

    def _sensor(self, vid: str, p: dict) -> list[str]:
        t = str(p.get("type", "?"))
        ts = float(p.get("sent_ts") or time.time())
        name = SENSOR_KO.get(t, t)
        ar = self.inv.area(vid)
        lab = self.inv.label(vid)
        head = f"[SENSOR]  {hhmmss(ts)}  {pad(name, 8)}{pad(ar, 8)}"
        extra: list[str] = []
        held = ""

        match t:
            case "presence":
                val = bool(p.get("present"))
                body = "있음" if val else "없음"
                held = self._held(vid, val, ts)
                extra.append(f"신뢰 {p.get('confidence', '-')}")
                if p.get("energy") is not None:
                    extra.append(f"에너지 {p['energy']}")
                if p.get("distance_cm"):
                    extra.append(f"거리 {p['distance_cm']}cm")
            case "motion":
                body = "감지" if p.get("motion") else "없음"
                extra.append(f"신뢰 {p.get('confidence', '-')}")
            case "bed":
                val = bool(p.get("occupied"))
                body = "눌림" if val else "해제"
                held = self._held(vid, val, ts)
            case "door":
                val = bool(p.get("open"))
                body = "열림" if val else "닫힘"
                held = self._held(vid, val, ts)
            case "power":
                watt = float(p.get("watt", 0.0))
                st = p.get("state")
                body = f"{watt:g}W" + (f"  {st}" if st else "")
                if st:
                    held = self._held(vid, st, ts)
            case "light":
                lux = float(p.get("illuminance_lux", 0.0))
                if not self._moved(vid, lux, self.LUX_EPS):
                    return []
                body = f"{lux:g} lux"
            case "climate":
                tc = float(p.get("temperature_c", 0.0))
                hp = float(p.get("humidity_pct", 0.0))
                moved_t = self._moved(f"{vid}:t", tc, self.TEMP_EPS)
                moved_h = self._moved(f"{vid}:h", hp, self.HUMID_EPS)
                if not (moved_t or moved_h):
                    return []
                self._last[f"{vid}:t"] = tc
                self._last[f"{vid}:h"] = hp
                body = f"{tc:g}℃  {hp:g}%"
            case _:
                body = json.dumps(
                    {k: v for k, v in p.items()
                     if k not in ("version", "sent_ts", "src_id", "type")},
                    ensure_ascii=False)

        # 지속시간과 라벨(냉장고·인덕션)은 첫 줄에. 둘째 줄은 판단 근거만
        if held:
            body = f"{body}  {held}"
        if lab:
            body = f"{pad(body, 26)}{lab}"
        return self.block(f"{head}{body}", *self.wrap(extra), indent=23)

    def _moved(self, key: str, value: float, eps: float) -> bool:
        prev = self._last.get(key)
        if prev is not None and abs(value - float(prev)) < eps:
            return False
        self._last[key] = value
        return True

    def _device_state(self, vid: str, p: dict) -> list[str]:
        dt = str(p.get("device_type", "?"))
        name = DEVICE_KO.get(dt, dt)
        ts = p.get("sent_ts")
        bits = [str(p["power"])] if p.get("power") else []
        for key, lab in (("brightness", "밝기"), ("color_temp", "색온"),
                         ("volume", "음량"), ("channel", "채널")):
            if p.get(key) is not None:
                bits.append(f"{lab} {p[key]}")
        body = "  ".join(bits) or "-"
        return [f"[DEVICE]  {hhmmss(ts)}  {pad(name, 8)}"
                f"{pad(self.inv.area(vid), 8)}{body}"]

    _EVENT_KO = {"remote_input": "리모컨 조작", "door_opened": "문 열림",
                 "door_closed": "문 닫힘", "dispensed": "배출"}

    def _device_event(self, vid: str, p: dict) -> list[str]:
        et = str(p.get("event_type", "?"))
        dt = str(p.get("device_type", "?"))
        name = DEVICE_KO.get(dt, dt)
        body = self._EVENT_KO.get(et, et)
        if et == "remote_input" and p.get("button"):
            body += f"  {p['button']}"
        if et == "door_closed" and p.get("open_duration_sec") is not None:
            body += f"  ({int(p['open_duration_sec'])}초)"
        return [f"[EVENT]   {hhmmss(p.get('sent_ts'))}  {pad(name, 8)}"
                f"{pad(self.inv.area(vid), 8)}{body}"]


# ─── 2. 활동 ─────────────────────────────────────────────────────────────

class ActivityView(View):
    title = "활동"

    def __init__(self, inv: Inventory) -> None:
        super().__init__(inv)
        self._prev: tuple[str, str] | None = None

    def topics(self) -> list[str]:
        return ["hestia/context/activity"]

    def handle(self, topic: str, p: dict) -> list[str]:
        state = str(p.get("state", "?"))
        facts = factors_ko(p.get("factors"))
        key = (state, " ".join(facts))
        if key == self._prev:
            return []
        self._prev = key

        ar = p.get("area") or p.get("user_area")
        where = f"@{area(ar)}" if ar else ""
        conf = p.get("confidence")
        conf_s = f"{float(conf):.2f}" if isinstance(conf, (int, float)) else "-"
        head = (f"[USER_STATE]  {hhmmss(p.get('sent_ts'))}  {pad(state, 15)}"
                f"{pad(ACTIVITY_KO.get(state, ''), 11)}{pad(where, 9)}{conf_s}")
        return self.block(head, *self.wrap(facts), indent=14) + [""]


# ─── 3. 판단 ─────────────────────────────────────────────────────────────

class DecisionView(View):
    title = "판단"

    def topics(self) -> list[str]:
        return ["hestia/context/presence", "hestia/context/away",
                "hestia/context/occupancy", "hestia/context/suppression",
                "hestia/model/+"]

    def handle(self, topic: str, p: dict) -> list[str]:
        name = topic.split("/")[-1]
        if topic.startswith("hestia/model/"):
            return self._model(name, p)

        ts = hhmmss(p.get("sent_ts"))
        conf = p.get("confidence")
        conf_s = f"{float(conf):.2f}" if isinstance(conf, (int, float)) else "-"
        facts = factors_ko(p.get("factors"))

        match name:
            case "presence":
                head = f"[PRESENCE]    {ts}  {pad(area(p.get('user_area')), 11)}{conf_s}"
                areas = p.get("areas") or {}
                rest = [" · ".join(f"{area(a)} {ko_bool(v)}" for a, v in areas.items())]
            case "away":
                head = f"[AWAY]        {ts}  {pad(p.get('state', '?'), 11)}{conf_s}"
                rest = [f"근거 {p['evidence']}"] if p.get("evidence") else []
            case "occupancy":
                head = f"[OCCUPANCY]   {ts}  {pad(p.get('state', '?'), 11)}{conf_s}"
                rest = []
            case "suppression":
                on = bool(p.get("active"))
                head = f"[SUPPRESS]    {ts}  {'활성' if on else '해제'}"
                bits = []
                if p.get("reason"):
                    bits.append(str(p["reason"]))
                if p.get("stage") is not None:
                    bits.append(f"단계 {p['stage']}")
                if p.get("until"):
                    bits.append(f"→ {hhmm(p['until'])}")
                rest = [" ".join(bits)] if bits else []
                if p.get("except"):
                    rest.append("예외 " + ", ".join(map(str, p["except"])))
                if p.get("cooldowns"):
                    rest.append("쿨다운 " + ", ".join(map(str, p["cooldowns"])))
                facts = []
            case _:
                return [f"[{name.upper()}]  {ts}  {json.dumps(p, ensure_ascii=False)}"]

        return self.block(head, *rest, *self.wrap(facts), indent=14)

    def _model(self, name: str, p: dict) -> list[str]:
        bits0 = [name]
        if p.get("sample_days") is not None:
            bits0.append(f"{p['sample_days']}일 학습")
        head = f"[MODEL]       {hhmmss(p.get('sent_ts'))}  {'   '.join(bits0)}"

        # predictability 는 distributions 안에 있기도 하고 최상위에
        # {kind: 값} 으로 오기도 한다 (RPi4 배치가 그렇게 보낸다)
        top_pred = p.get("predictability")
        rest: list[str] = []
        dists = p.get("distributions")
        if not isinstance(dists, dict):
            if dists is not None:
                rest.append(json.dumps(dists, ensure_ascii=False))
            return self.block(head, *rest, indent=14)

        for kind, d in dists.items():
            if not isinstance(d, dict):
                rest.append(f"{pad(kind, 11)}{d}")
                continue
            bits = []
            peaks = [x for x in (d.get("peaks") or []) if x is not None]
            if peaks:
                bits.append("피크 " + " / ".join(self._peak(x) for x in peaks))

            pred = d.get("predictability")
            if pred is None and isinstance(top_pred, dict):
                pred = top_pred.get(kind)
            if pred is not None:
                bits.append(f"예측도 {_f2(pred)}")

            ratio = d.get("days_ratio")
            if ratio is None and peaks and isinstance(peaks[0], dict):
                ratio = peaks[0].get("days_ratio")
            if ratio is not None:
                bits.append(f"관측일 비율 {_f2(ratio)}")

            rest.append(pad(kind, 11) + "   ".join(bits) if bits
                        else f"{pad(kind, 11)}{json.dumps(d, ensure_ascii=False)}")
        return self.block(head, *rest, indent=14)

    @staticmethod
    def _peak(x: Any) -> str:
        """봉우리 한 개. 분 단위 정수든 {center, from, to} 든 읽는다."""
        span = ""
        if isinstance(x, dict):
            lo, hi = x.get("from"), x.get("to")
            if lo is not None and hi is not None:
                span = f"[{_hm(lo)}~{_hm(hi)})"
            for key in ("center", "minute", "t", "peak"):
                if x.get(key) is not None:
                    x = x[key]
                    break
            else:
                return json.dumps(x, ensure_ascii=False)
        return f"{_hm(x)}{span}"


# ─── 4. 개입 ─────────────────────────────────────────────────────────────

class ActionView(View):
    title = "개입"

    def topics(self) -> list[str]:
        return ["hestia/intervention/decision", "hestia/intervention/outcome",
                "hestia/notify/push", "hestia/notify/speak",
                "hestia/notify/ack", "hestia/notify/cancel",
                "hestia/device/+/cmd"]

    def handle(self, topic: str, p: dict) -> list[str]:
        ts = hhmmss(p.get("sent_ts"))
        if topic.endswith("/cmd"):
            return self._cmd(topic.split("/")[2], ts, p)
        match topic.split("/")[-1]:
            case "decision":
                return self._decision(ts, p)
            case "outcome":
                return self._outcome(ts, p)
            case "push":
                return self._push(ts, p)
            case "speak":
                return [f'[SPEAK]     {ts}  "{p.get("text", "")}"']
            case "ack":
                return [f"[ACK]       {ts}  {p.get('notify_id', '-')}   "
                        f"{p.get('ack_type', '-')}"]
            case "cancel":
                return [f"[CANCEL]    {ts}  {p.get('notify_id', '-')}"]
        return []

    def _decision(self, ts: str, p: dict) -> list[str]:
        conf = p.get("confidence")
        conf_s = f"{float(conf):.2f}" if isinstance(conf, (int, float)) else "-"
        head = (f"[DECISION]  {ts}  {str(p.get('kind', '?')):<10} "
                f"후보 {ko_bool(p.get('candidate'))}   "
                f"{str(p.get('reason', '-')):<18} {conf_s}")
        rest: list[str] = []
        bits = []
        if p.get("tail_probability") is not None:
            bits.append(f"꼬리 {p['tail_probability']}")
        if p.get("predictability") is not None:
            bits.append(f"예측도 {p['predictability']}")
        if bits:
            rest.append(" · ".join(bits))
        rest += self.wrap(factors_ko(p.get("factors")))
        return self.block(head, *rest, indent=12)

    def _outcome(self, ts: str, p: dict) -> list[str]:
        """개입이 먹혔는지. ack 와 다르다 — ack 는 기기가 띄웠다는 것뿐이고
        complied 는 사용자가 실제로 했는지다."""
        r = p.get("user_response") or {}
        bits = [f"확인 {ko_bool(r.get('acked'))}", f"이행 {ko_bool(r.get('complied'))}"]
        tried = r.get("channels_tried")
        if tried:
            bits.append("시도 " + ", ".join(map(str, tried)))
        head = f"[OUTCOME]   {ts}  {pad(p.get('scenario', '?'), 20)}"
        return self.block(head + " · ".join(bits), indent=12)

    def _push(self, ts: str, p: dict) -> list[str]:
        # channels 는 채널 종류가 아니라 기기 vid 다
        ch = ", ".join(self._ch(c) for c in p.get("channels", [])) or "-"
        head = (f"[NOTIFY]    {ts}  {pad(p.get('scenario', '?'), 20)}"
                f"{pad('@' + area(p.get('area')), 9)}{ch}")
        body = p.get("payload") or {}
        text = str(body.get("text", body.get("title", "")))
        # 알림 문구에 줄바꿈이 들어온다 — 한 줄로 붙인다
        rest = ['"' + " ".join(text.split()) + '"']
        bits = []
        if p.get("requires_ack"):
            bits.append("ack 필요")
            if p.get("ack_deadline"):
                bits.append(f"{hhmm(p['ack_deadline'])} 까지")
        else:
            bits.append("ack 불필요")
        bits.append(f"에스컬레이션 {p.get('escalation_level', 0)}")
        if p.get("priority") and str(p["priority"]).upper() != "NORMAL":
            bits.append(f"우선도 {p['priority']}")
        rest.append(" · ".join(bits))
        if p.get("proxy_for"):
            pf = str(p["proxy_for"])
            rest.append(f"대행 {DEVICE_KO.get(pf, self.inv.label(pf) or pf)}")
        return self.block(head, *rest, indent=12)

    def _ch(self, vid: Any) -> str:
        """channels 는 채널 종류가 아니라 수신 기기의 vid 다."""
        v = str(vid)
        lab = self.inv.label(v)
        return f"{lab}({v})" if lab else v

    _PARAM_KO = {"power": "", "brightness": "밝기", "color_temp": "색온",
                 "volume": "음량", "channel": "채널", "transition_ms": "전환"}

    def _cmd(self, vid: str, ts: str, p: dict) -> list[str]:
        dt = str(p.get("device_type", "?"))
        name = DEVICE_KO.get(dt, dt)
        bits = [str(p.get("action", "?"))]
        params = p.get("params") or {}
        for key, v in params.items():
            if v is None:
                continue
            lab = self._PARAM_KO.get(key, key)
            bits.append(f"{lab} {v}".strip())
        head = (f"[CONTROL]   {ts}  {pad(name, 8)}{pad(self.inv.area(vid), 8)}"
                f"{'  '.join(bits)}")
        return self.block(head, str(p.get("reason", "")), indent=12)


# ─── 5. 상황판 ───────────────────────────────────────────────────────────

class BoardView(View):
    """로그가 아니다. 한 화면을 계속 갈아끼운다."""

    title = "현재 상태"
    W = 42

    def __init__(self, inv: Inventory) -> None:
        super().__init__(inv)
        self.act: dict = {}
        self.pres: dict = {}
        self.away: dict = {}
        self.occ: dict = {}
        self.sup: dict = {}
        self.nodes: dict[str, dict] = {}
        self.daily: dict | None = None     # context/day — retained, 오늘 치가 다 들어온다
        self.count: dict[str, int] = {}    # context/day 가 없을 때 t0 를 직접 센다
        self.day = time.strftime("%Y-%m-%d")

    def topics(self) -> list[str]:
        return ["hestia/context/activity", "hestia/context/presence",
                "hestia/context/away", "hestia/context/occupancy",
                "hestia/context/suppression", "hestia/context/day",
                "hestia/log/t0",
                "hestia/node/+/announce", "hestia/node/+/status",
                "hestia/registry/devices"]

    def handle(self, topic: str, p: dict) -> list[str]:
        match topic.split("/")[1:]:
            case ["context", "activity"]:
                self.act = p
            case ["context", "presence"]:
                self.pres = p
            case ["context", "away"]:
                self.away = p
            case ["context", "occupancy"]:
                self.occ = p
            case ["context", "suppression"]:
                self.sup = p
            case ["context", "day"]:
                self.daily = p
            case ["log", "t0"]:
                if p.get("date") == self.day:
                    t = str(p.get("type", ""))
                    self.count[t] = self.count.get(t, 0) + 1
            case ["node", nid, "announce"]:
                n = self.nodes.setdefault(nid, {})
                em = [e for e in p.get("emulates", []) if isinstance(e, dict)]
                n["sensors"] = sum(1 for e in em if e.get("type"))
                n["devices"] = sum(1 for e in em if e.get("device_type"))
            case ["node", nid, "status"]:
                n = self.nodes.setdefault(nid, {})
                n["online"] = bool(p.get("online"))
                n["ts_synced"] = bool(p.get("ts_synced"))
            case ["registry", "devices"]:
                self.inv.apply_registry(p.get("devices", []))
        return []

    def render(self) -> str:
        W = self.W
        L: list[str] = [" ┌" + "─" * (W + 2) + "┐"]

        def row(s: str = "") -> None:
            L.append(f" │ {pad(s, W)} │")

        clock = time.strftime("%H:%M:%S")
        row(f"현재 상태{' ' * (W - cols('현재 상태') - len(clock))}{clock}")
        row("─" * W)
        row()

        state = str(self.act.get("state", "-"))
        ar = self.act.get("area") or self.pres.get("user_area")
        row(f"  {area(ar)}")
        row(self._kv(f"  {ACTIVITY_KO.get(state, state)}  ({state})",
                     "", self.act.get("confidence")))
        if self.act.get("since"):
            row(f"  {hhmm(self.act['since'])} 부터")
        row()

        areas = self.pres.get("areas") or {}
        here = " + ".join(area(a) for a, v in areas.items() if v) or "-"
        row(self._kv("  재실", here, self.pres.get("confidence")))
        row(self._kv("  부재", self.away.get("state", "-"), self.away.get("confidence")))
        row(self._kv("  인원", self.occ.get("state", "-"), self.occ.get("confidence")))
        if self.sup.get("active"):
            until = f" → {hhmm(self.sup['until'])}" if self.sup.get("until") else ""
            row(f"  억제   {self.sup.get('reason', '활성')}"
                f" 단계 {self.sup.get('stage', '-')}{until}")
        else:
            row("  억제   없음")
        row()
        meals, hyd, med = self._today()
        row(f"  오늘   끼니 {meals}   수분 {hyd}   복약 {med}")
        row()

        if not self.nodes:
            row("  노드 보고 없음")
        for nid, n in sorted(self.nodes.items()):
            up = "online" if n.get("online") else "offline"
            ntp = f"   NTP {ko_bool(n['ts_synced'])}" if "ts_synced" in n else ""
            row(f"  {pad(nid, 17)}{up}{ntp}")
            if "sensors" in n:
                row(f"  {' ' * 17}센서 {n['sensors']} · 가전 {n['devices']}")

        L.append(" └" + "─" * (W + 2) + "┘")
        return "\n".join(L)

    def _today(self) -> tuple[int, int, int]:
        """오늘 몇 번. context/day 가 정본이다 — retained 라 모니터를 늦게
        켜도 오늘 치가 다 들어온다. 없으면 받은 t0 만 센다(켜기 전은 못 셈)."""
        d = self.daily
        if isinstance(d, dict):
            def n(key: str) -> int:
                v = d.get(key)
                return len(v) if isinstance(v, (list, tuple)) else 0
            return n("meals"), n("hydrations"), n("medications")
        return (self.count.get("meal", 0), self.count.get("hydration", 0),
                self.count.get("medication", 0))

    def _kv(self, label: str, value: Any, conf: Any) -> str:
        c = f"{float(conf):.2f}" if isinstance(conf, (int, float)) else ""
        left = f"{label}   {value}".rstrip()
        gap = max(1, self.W - cols(left) - len(c))
        return f"{left}{' ' * gap}{c}"


# ─── 6. 기록 ─────────────────────────────────────────────────────────────

class T0View(View):
    title = "기록"

    def __init__(self, inv: Inventory) -> None:
        super().__init__(inv)
        # 엔진은 sleep_start/sleep_end 둘 다 duration_sec=0 으로 보낸다.
        # 수면 길이는 두 건을 짝지어야 나온다 (배치도 그렇게 한다)
        self._sleep_t0: float | None = None

    def topics(self) -> list[str]:
        return ["hestia/log/t0"]

    def handle(self, topic: str, p: dict) -> list[str]:
        t = str(p.get("type", "?"))
        tag = T0_TAG.get(t, t.upper()[:5])
        head = (f"{pad(f'[{tag}]', 8)}{hhmmss(p.get('sent_ts'))}  "
                f"{pad(T0_KO.get(t, t), 11)}")

        bits: list[str] = []
        if t == "sleep_end":
            # 시작을 봤으면 길이를 직접 잰다
            end = p.get("t0")
            if self._sleep_t0 is not None and end:
                bits.append(f"{hhmm(self._sleep_t0)} → {hhmm(end)}")
                bits.append(dur(float(end) - self._sleep_t0))
            self._sleep_t0 = None
            if p.get("source") and p["source"] != "sensor":
                bits.append(f"출처 {p['source']}")
            return [head + "   ".join(bits)]

        if t == "sleep_start":
            if p.get("t0"):
                self._sleep_t0 = float(p["t0"])
            if p.get("area"):
                bits.append(f"@{area(p['area'])}")
            if p.get("method"):
                bits.append(str(p["method"]))
            if p.get("confidence") is not None:
                bits.append(f"확신 {_f2(p['confidence'])}")
            if p.get("awake_areas"):
                bits.append("깬 구역 " + ", ".join(area(a) for a in p["awake_areas"]))
        else:
            # duration 은 묶음(t0) 길이, eat_t0 는 실제로 먹기 시작한 시각.
            # 둘을 같은 줄에 그냥 두면 모순으로 읽힌다
            if p.get("t0"):
                span = f"묶음 {hhmm(p['t0'])}"
                if p.get("duration_sec"):
                    span += f" +{dur(p['duration_sec'])}"
                bits.append(span)
            elif p.get("duration_sec"):
                bits.append(dur(p["duration_sec"]))
            if p.get("eat_t0"):
                bits.append(f"식사 시작 {hhmm(p['eat_t0'])}")
            # 수면 종료에 '유도'는 뜻이 없다. 식사·수분·복약만
            if t != "sleep_end":
                bits.append(f"유도 {ko_bool(p.get('prompted'))}")
        if p.get("source") and p["source"] != "sensor":
            bits.append(f"출처 {p['source']}")
        return [head + "   ".join(bits)]


VIEWS: dict[str, type[View]] = {
    "input": InputView, "activity": ActivityView, "decision": DecisionView,
    "action": ActionView, "board": BoardView, "t0": T0View,
}


# ─── 구동 ────────────────────────────────────────────────────────────────

CLEAR = "\033[2J\033[H"


def main() -> int:
    ap = argparse.ArgumentParser(description="HESTIA 시연 모니터")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--view", required=True, choices=sorted(VIEWS))
    ap.add_argument("--config", type=Path, default=None,
                    help="demo.toml. vid → 구역·라벨 (registry retained 가 오면 덮어씀)")
    args = ap.parse_args()

    inv = Inventory()
    if args.config:
        inv.load_config(args.config)

    view = VIEWS[args.view](inv)
    board = isinstance(view, BoardView)

    print(CLEAR if board else view.banner(), end="", flush=True)

    def on_connect(client, userdata, flags, rc, properties=None):
        if getattr(rc, "value", rc) != 0:
            print(f"  브로커 연결 실패 rc={rc}", file=sys.stderr)
            return
        for t in view.topics():
            client.subscribe(t, qos=0)

    def on_message(client, userdata, msg):
        # 빈 페이로드는 retained 삭제 신호다 (simulate 의 clear). 조용히 넘긴다
        if not msg.payload or not msg.payload.strip():
            return
        try:
            p = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            print(f"  파싱 실패 {msg.topic}", file=sys.stderr)
            return
        if not isinstance(p, dict):
            return
        try:
            lines = view.handle(msg.topic, p)
        except Exception as exc:                      # 창이 죽는 게 최악이다
            print(f"  처리 실패 {msg.topic}: {exc}", file=sys.stderr)
            return
        if board:
            print(CLEAR + view.render(), flush=True)
            return
        for line in lines:
            print(line, flush=True)

    cid = f"monitor-{args.view}-{int(time.time())}"
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
    except AttributeError:                            # paho 1.x
        client = mqtt.Client(client_id=cid)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(args.host, args.port, keepalive=60)
    except OSError as exc:
        print(f"  {args.host}:{args.port} 에 연결 못 함: {exc}", file=sys.stderr)
        return 1

    client.loop_start()
    try:
        while True:
            if board:
                print(CLEAR + view.render(), flush=True)
                time.sleep(1.0)                       # 시계를 돌려야 한다
            else:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())