"""가전·센서 시뮬레이터.

엔진을 실기기 없이 돌려보기 위한 도구다. 패키지 밖에 두는 것은
엔진의 일부가 아니라 엔진을 테스트하는 물건이기 때문이다.

    cmd 수신 → 내부 상태 변경 → state 재발행 → 엔진이 결과를 본다

양방향인 것이 핵심이다. mosquitto_pub 으로는 state 를 쏠 수 있지만
cmd 를 받을 수 없어서, 제어가 먹혔는지 확인할 방법이 없다.

엔진이 내보내는 것(context, 알림, 판정, t0 로그)도 함께 구독해 찍는다 —
쏘기만 하고 결과를 못 보면 터미널 두 개를 번갈아 봐야 한다.

센서 값은 센서가 보내는 그대로 적는다. 시뮬레이터가 대신 고르지 않는다 —
'still' 이 energy=3 을 끼워 넣어서, 맥에서는 EATING 이 뜨고 실물에서는
안 뜨던 일이 있었다 (2026-10-16). 편의 계층이 버그를 숨긴다.

재실은 배타적이다. 사람은 한 명이고 한 곳에만 있으므로, 어느 구역을
켜면 나머지 구역을 비운다 — 실제 mmWave 도 사람이 나가면 스스로
present=false 를 올린다. 두 명을 만들어 보려면 끝에 'keep'.

presence 는 20초마다 저절로 다시 나간다 (펌웨어 PERIOD_MS 와 같다).
그게 없으면 손으로 쏠 때만 엔진이 깨어나, dwell·still_sec 같은
지속 임계값이 시각이 지나도 안 걸린다 — activity.py 가 거기에
타이머를 예약하지 않기 때문이다. 'heartbeat off' 로 끌 수 있다.

점진 변화는 중간값을 보내지 않는다 — transition_ms 만큼 기다렸다가
최종값 하나만 발행한다. 실제 기기도 대개 그렇고, 중간값이 오면
엔진의 되돌림 판정이 그것을 사용자 조작으로 오인한다.

시연에서는 'clock' 으로 시각을 옮긴다. 메시지에 실리는 시각과 화면에
찍는 시각만 가상이고, 내부 스케줄링(하트비트·전환 대기·wait)은 실제
시간을 쓴다 — 점프가 그것들을 흔들면 안 된다.

실행:
    python tools/simulate.py
    python tools/simulate.py --host 192.168.0.14 --config ./config-test
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paho.mqtt.client as mqtt

from hestia_engine.config import Config, ConfigError, load
from hestia_engine.messages import DeviceCommand, parse

SRC_VERSION = 1

# 가전마다 기본 상태가 다르다. 냉장고는 power 가 없고 문이 닫혀
# 있는 것이 기본이다 (명세).
INITIAL = {
    "smart_fridge": {"door": "CLOSED"},
    "washer": {"power": "OFF", "cycle": "DONE"},
    "robot_cleaner": {"status": "DOCKED", "battery": 100},
    "display_node": {"power": "ON", "display": ""},
    "cooktop": {"status": "INITIAL"}, 
}
DEFAULT_INITIAL = {"power": "OFF"}

# action 별로 바뀌는 필드 (명세). set 은 params 를 그대로 병합한다.
ACTION_EFFECT = {
    "start": {"status": "CLEANING"},
    "stop": {"status": "PAUSED"},
    "dock": {"status": "DOCKED"},
}

TRANSITION_KEY = "transition_ms"

# mmWave 가 보고하는 타깃 상태 (LD2410). 사물은 STATIC 까지만 만든다.
TARGETS = ("NONE", "MOVING", "STATIC", "BOTH")

TRUE_WORDS = ("true", "t", "1", "on", "yes", "y")
FALSE_WORDS = ("false", "f", "0", "off", "no", "n")

# 시연용 시계. 엔진과 모니터가 구독한다. retained 라 늦게 켠 창도 따라온다.
CLOCK_TOPIC = "hestia/sim/clock"

# 엔진이 내보내는 것들. 구독해서 찍는다.
WATCH_TOPICS = [
    ("hestia/context/+", 1),
    ("hestia/notify/push", 1),
    ("hestia/notify/speak", 1),
    ("hestia/notify/cancel", 1),
    ("hestia/intervention/decision", 1),
    ("hestia/intervention/outcome", 1),
    ("hestia/log/t0", 1),
]

GRID_STEP = 15
GRID_N = 1440 // GRID_STEP

USAGE_PRESENCE = """사용법: presence <area> <true|false> [target] [sp] [d=N] [keep]
  present 가 true 면 target 과 sp 를 반드시 적는다 (기본값을 끼워 넣지 않는다)
    target  NONE | MOVING | STATIC | BOTH
    sp      dist_spread_cm — 최근 10초 거리 변동폭
    d=N     distance_cm (기본 150)
    keep    다른 구역을 비우지 않는다 — 2인 상황 테스트용
  예)
    presence kitchen true BOTH 90      걸어 들어옴
    presence kitchen true BOTH 30      앉아서 식사 — 팔은 움직이고 자리는 그대로
    presence bedroom true STATIC 3     완전 정지 — 자는 사람·쓰러진 사람
    presence kitchen false             이탈 (NONE / 0 으로 나간다)"""

USAGE_CLOCK = """사용법: clock [시각|간격]
  clock 09:00      다음 09:00 으로. 현재보다 뒤인 가장 가까운 그 시각이다
  clock +9m30s     상대. +4h, +570(초) 도 된다
  clock            현재 오프셋과 가상 시각

  시계는 되돌릴 수 없다. 22:00 에서 clock 02:00 은 다음날 02:00 이다.
  점프 전에 idle 을 쳐라 — 수면 장면만 예외다."""


def _as_bool(token: str) -> bool | None:
    """true/false 를 읽는다. on/off, 1/0 도 받는다 — 손이 기억하는 말들이다."""
    t = token.lower()
    if t in TRUE_WORDS:
        return True
    if t in FALSE_WORDS:
        return False
    return None


def _parse_duration(text: str) -> float:
    """'9m30s' / '4h' / '570' → 초."""
    text = text.strip().lower()
    if not text:
        raise ValueError("빈 간격")
    if text.isdigit():
        return float(text)
    total, num = 0.0, ""
    for ch in text:
        if ch.isdigit():
            num += ch
            continue
        if not num or ch not in "hms":
            raise ValueError(f"모르는 간격: {text}")
        total += int(num) * {"h": 3600, "m": 60, "s": 1}[ch]
        num = ""
    if num:
        raise ValueError(f"단위가 없다: {text}")
    return total


class Simulator:
    """가전 상태를 들고 cmd 에 반응한다. 센서는 손으로 쏜다."""
    LUX_PER_BRIGHTNESS = 4.0

    def __init__(self, config: Config, host: str, port: int, *, watch: bool = True) -> None:
        self._config = config
        self._host = host
        self._port = port
        self._running = False
        self._watch = watch

        self.devices: dict[str, dict[str, Any]] = {
            d.id: dict(INITIAL.get(d.device_type, DEFAULT_INITIAL))
            for d in config.all_devices()
            if d.enabled
        }
        self._seq: dict[str, int] = {}
        self._cmd_seq: int = 0             # 시뮬레이터가 쏘는 cmd 의 cmd_id
        # (적용할 시각, vid, params) — transition_ms 를 블로킹 없이 기다린다
        self._pending: list[tuple[float, str, dict[str, Any]]] = []
        # (실행할 시각, 설명, 함수) — 시나리오 매크로의 단계들
        self._script: list[tuple[float, str, Callable[[], None]]] = []
        self._input: queue.Queue[str] = queue.Queue()

        self._last_notify_id: str | None = None
        self._last_context: dict[str, str] = {}
        self._fridge_opened_at: float | None = None
        self._lux_base: float = 100.0      # lux 명령이 준 '조명을 뺀' 바닥 조도
        self._lux_owned: bool = True   # False 면 실물 조도 센서가 맡는다

        # 시연용 가상 시계. clock 명령이 바꾸고 CLOCK_TOPIC 으로 알린다.
        # 메시지에 실리는 시각과 화면에 찍는 시각만 이것을 쓴다 —
        # 내부 스케줄링은 실제 시간이다. 점프가 흔들면 안 되기 때문이다.
        self._offset: float = 0.0

        # 마지막으로 쏜 presence 값. 실제 노드처럼 주기적으로 다시 쏜다
        self._presence_last: dict[str, dict[str, Any]] = {}
        self._hb_period: float = 20.0      # 펌웨어 PERIOD_MS 와 같은 역할
        self._hb_next: float = time.time() + 20.0

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"hestia-sim-{os.getpid()}"
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    # ------------------------------------------------------------ 가상 시계

    def _now(self) -> float:
        """메시지에 실을 시각. 화면에 찍는 시각도 이것이다."""
        return time.time() + self._offset

    def _now_dt(self) -> datetime:
        return datetime.fromtimestamp(self._now())

    # ------------------------------------------------------------ 생애

    def run(self) -> None:
        """MQTT 루프와 입력 큐를 번갈아 돈다.

        입력은 별도 스레드가 읽어 큐에 넣는다 — 상태는 메인 루프에서만
        만지므로 락이 필요 없다.
        """
        self._running = True
        threading.Thread(target=self._read_input, daemon=True).start()

        try:
            self._client.connect(self._host, self._port, keepalive=60)
        except OSError as exc:
            print(f"브로커 연결 실패: {exc}")
            return

        print(
            f"브로커 {self._host}:{self._port} — help 로 명령 목록"
            f"\npresence 하트비트 {self._hb_period:g}초 (heartbeat off 로 끔)"
        )
        while self._running:
            self._client.loop(timeout=0.1)
            self._apply_due()
            self._run_heartbeat()
            self._run_script()
            self._drain_input()

        self._client.disconnect()

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------ MQTT

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            print(f"연결 거부 rc={reason_code}")
            return
        client.subscribe(CLOCK_TOPIC, qos=1)
        client.subscribe("hestia/device/+/cmd", qos=1)
        if self._watch:
            for topic, qos in WATCH_TOPICS:
                client.subscribe(topic, qos)

        # 브로커가 껐다 켜지면 retained 가 사라진다. 다시 쏴야
        # 엔진이 현재 상태를 복원한다.
        for vid in self.devices:
            self._publish_state(vid)
        print(f"연결됨 — 가전 {len(self.devices)}대 상태 발행")

    def _on_message(self, client, userdata, msg) -> None:
        topic = msg.topic

        if topic == CLOCK_TOPIC:
            self._sync_clock(msg.payload)
            return

        if topic.endswith("/cmd"):
            try:
                cmd = parse(topic, msg.payload, self._now())
            except Exception as exc:                  # noqa: BLE001
                print(f"파싱 실패: {exc}")
                return
            if isinstance(cmd, DeviceCommand):
                self._handle_cmd(cmd)
            return

        if self._watch:
            self._show_incoming(topic, msg.payload)

    # ------------------------------------------------------------ 엔진 출력

    def _show_incoming(self, topic: str, raw: bytes) -> None:
        """엔진이 내보낸 것을 한 줄로 찍는다."""
        try:
            p = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        stamp = self._now_dt().strftime("%H:%M:%S")

        if topic == "hestia/notify/push":
            self._last_notify_id = p.get("notify_id")
            body = p.get("payload", {})
            proxy = f" [{p.get('proxy_for')} 대행]" if p.get("proxy_for") else ""
            print(
                f"\n{stamp} ■ 알림 {p.get('scenario')} → {p.get('channels')}{proxy}"
                f"\n         {body.get('title')} / {body.get('text')}"
                f"\n         notify_id={p.get('notify_id')} "
                f"ack={p.get('requires_ack')} lv={p.get('escalation_level')}"
            )
            return

        if topic == "hestia/notify/speak":
            print(f"{stamp} ♪ 음성 {p.get('text')}")
            return

        if topic == "hestia/notify/cancel":
            print(f"{stamp} ✕ 취소 {p.get('notify_id')}")
            return

        if topic == "hestia/intervention/decision":
            mark = "○" if p.get("candidate") else "·"
            tail = p.get("tail_probability")
            pred = p.get("predictability")
            extra = ""
            if tail is not None:
                extra = f" tail={tail:.3f}"
                if pred is not None:
                    extra += f" pred={pred:.2f}"
            peak = p.get("factors", {}).get("peak_center")
            if peak is not None:
                extra += f" peak={peak // 60}:{peak % 60:02d}"
            print(f"{stamp} {mark} 판정 {p.get('kind')} {p.get('reason')}{extra}")
            return

        if topic == "hestia/intervention/outcome":
            r = p.get("user_response", {})
            print(
                f"{stamp} ◆ 결과 {p.get('scenario')} "
                f"acked={r.get('acked')} complied={r.get('complied')} "
                f"채널={r.get('channels_tried')}"
            )
            return

        if topic == "hestia/log/t0":
            when = datetime.fromtimestamp(p.get("t0", 0)).strftime("%H:%M")
            extra = ""
            if p.get("eat_t0"):
                extra = f" eat_t0={datetime.fromtimestamp(p['eat_t0']).strftime('%H:%M')}"
            if p.get("method"):
                extra += f" method={p['method']}"
            print(
                f"{stamp} ▣ t0 {p.get('type')} {when}"
                f" dur={p.get('duration_sec', 0):.0f}s{extra}"
            )
            return

        if topic.startswith("hestia/context/"):
            name = topic.rsplit("/", 1)[-1]
            line = self._context_line(name, p)
            if line is None or self._last_context.get(name) == line:
                return                                # 안 바뀌었으면 조용히
            self._last_context[name] = line
            print(f"{stamp} → {name:11} {line}")

    @staticmethod
    def _context_line(name: str, p: dict[str, Any]) -> str | None:
        match name:
            case "activity":
                area = f" @{p['area']}" if p.get("area") else ""
                return f"{p.get('state')}{area} conf={p.get('confidence')}"
            case "presence":
                here = [a for a, v in (p.get("areas") or {}).items() if v]
                return f"user_area={p.get('user_area')} 재실={here}"
            case "away":
                return f"{p.get('state')} ({p.get('evidence')})"
            case "occupancy":
                return str(p.get("state"))
            case "suppression":
                if not p.get("active") and not p.get("cooldowns"):
                    return "없음"
                return f"{p.get('reason')} cooldowns={p.get('cooldowns')}"
            case "day":
                def hhmm(ts: float) -> str:
                    return datetime.fromtimestamp(ts).strftime("%H:%M")
                return (
                    f"meals={[hhmm(t) for t in p.get('meals', [])]} "
                    f"hyd={len(p.get('hydrations', []))} "
                )
        return None

    # ------------------------------------------------------------ cmd 처리

    def _handle_cmd(self, cmd: DeviceCommand) -> None:
        if cmd.target not in self.devices:
            print(f"모르는 기기: {cmd.target}")
            return

        if cmd.action == "set":
            params = {k: v for k, v in cmd.params.items() if k != TRANSITION_KEY}
            delay_ms = cmd.params.get(TRANSITION_KEY, 0)
        else:
            params = dict(ACTION_EFFECT.get(cmd.action, {}))
            delay_ms = 0

        if not params:
            return

        label = " ".join(f"{k}={v}" for k, v in params.items())
        if delay_ms:
            # 전환 중에는 아무것도 보내지 않는다. 노드가 보간하고
            # 끝난 뒤 최종값만 보고하는 것이 실제 기기의 동작이다.
            print(f"  ← cmd {cmd.target} {cmd.action} {label} ({delay_ms}ms 전환)")
            self._pending.append((time.time() + delay_ms / 1000.0, cmd.target, params))
        else:
            print(f"  ← cmd {cmd.target} {cmd.action} {label} [{cmd.reason}]")
            self._set(cmd.target, **params)

    def _apply_due(self) -> None:
        if not self._pending:
            return
        now = time.time()
        due = [p for p in self._pending if p[0] <= now]
        self._pending = [p for p in self._pending if p[0] > now]
        for _, vid, params in due:
            print(f"  전환 완료 {vid} {params}")
            self._set(vid, **params)

    # ------------------------------------------------------------ 하트비트

    def _run_heartbeat(self) -> None:
        """실제 노드처럼 주기적으로 같은 presence 값을 다시 쏜다.

        펌웨어 PERIOD_MS 와 같은 역할이다. 이게 없으면 손으로 쏠 때만
        엔진이 깨어나, dwell·still_sec 같은 지속 임계값이 시각이 지나도
        안 걸린다 — activity.py 가 거기에 타이머를 예약하지 않는다.

        조용하다. _publish_sensor 는 찍지 않고, context 는 바뀔 때만
        찍히므로 화면이 지저분해지지 않는다.
        """
        if self._hb_period <= 0 or not self._presence_last:
            return
        now = time.time()
        if now < self._hb_next:
            return
        self._hb_next = now + self._hb_period
        for vid, fields in self._presence_last.items():
            self._publish_sensor(vid, **fields)

    def _heartbeat(self, args: list[str]) -> None:
        """heartbeat [초|off] — presence 재발행 주기. 기본 20초."""
        if args:
            if args[0].lower() in ("off", "0"):
                self._hb_period = 0.0
                print("  하트비트 꺼짐 — 손으로 쏠 때만 엔진이 깨어난다")
                return
            try:
                self._hb_period = float(args[0])
            except ValueError:
                print("사용법: heartbeat [초|off]")
                return
        if self._hb_period <= 0:
            print("  하트비트 꺼짐")
            return
        self._hb_next = time.time() + self._hb_period
        print(
            f"  하트비트 {self._hb_period:g}초 — "
            f"presence {len(self._presence_last)}개 재발행"
        )

    # ------------------------------------------------------------ 발행

    def _set(self, vid: str, **changes: Any) -> None:
        """내부 상태를 바꾸고 state 를 발행한다."""
        self.devices.setdefault(vid, {}).update(changes)

        # 조도를 먼저 보낸다. 기기 상태가 먼저 가면 엔진이 묵은 조도로
        # 판정해 한 단계 더 올린다. 조도가 먼저 가면 밝기만 한 틱 묵는데,
        # 그건 '올릴지 말지' 가 아니라 '얼마나' 에만 영향을 준다.
        d = self._config.device(vid)
        if d is not None and d.device_type == "smart_light" and self._lux_owned:
            self._publish_lux()

        self._publish_state(vid)

    def _publish_state(self, vid: str) -> None:
        d = self._config.device(vid)
        if d is None:
            return
        payload = {
            "version": SRC_VERSION,
            "sent_ts": int(self._now()),
            "src_id": vid,
            "seq": self._next_seq(vid),
            "device_type": d.device_type,
            "source": d.source,
            **self.devices[vid],
        }
        # state 는 retained — 엔진이 재시작해도 현재 상태를 안다 (명세)
        self._client.publish(
            f"hestia/device/{vid}/state", json.dumps(payload, ensure_ascii=False),
            qos=1, retain=True,
        )

    def _publish_event(self, vid: str, event_type: str, **fields: Any) -> None:
        d = self._config.device(vid)
        if d is None:
            print(f"모르는 기기: {vid}")
            return
        payload = {
            "version": SRC_VERSION,
            "sent_ts": int(self._now()),
            "src_id": vid,
            "seq": self._next_seq(vid),
            "device_type": d.device_type,
            "source": d.source,
            "event_type": event_type,
            **fields,
        }
        # 이벤트는 retained 가 아니다 — 순간의 사건이라 보관하면
        # 재연결 때 과거 급수가 방금 일어난 것처럼 보인다 (명세)
        self._client.publish(
            f"hestia/device/{vid}/event", json.dumps(payload, ensure_ascii=False),
            qos=1, retain=False,
        )

    def _publish_sensor(self, vid: str, **fields: Any) -> None:
        s = self._config.sensor(vid)
        if s is None:
            print(f"모르는 센서: {vid}")
            return
        payload = {
            "version": SRC_VERSION,
            "sent_ts": int(self._now()),
            "src_id": vid,
            "seq": self._next_seq(vid),
            "type": s.type,
            **fields,
        }
        retain = s.type != "motion"      # motion 은 순간값이라 보관 안 함 (명세)
        self._client.publish(
            f"hestia/sensor/{vid}/state", json.dumps(payload, ensure_ascii=False),
            qos=1, retain=retain,
        )

    def _publish_raw(self, topic: str, payload: dict[str, Any], *, retain: bool) -> None:
        """센서도 가전도 아닌 것 — 모델, 복약 등록, ack."""
        self._client.publish(
            topic, json.dumps(payload, ensure_ascii=False), qos=1, retain=retain
        )

    def _publish_cmd(self, vid: str, device_type: str, action: str,
                     params: dict[str, Any], *,
                     reason: str = "USER_MANUAL") -> None:
        """사용자가 기기를 손으로 조작한 것을 흉내낸다.

        엔진이 쏘는 것과 같은 모양이다 (control.py 의 _publish).
        ESP32 는 cmd_id / device_type / action 이 비어있지 않은지만 보고
        (payload.cpp:421) 실제 동작은 params 로 결정한다 — version 과
        src_id 는 검사하지 않는다.
        """
        self._cmd_seq += 1
        self._publish_raw(
            f"hestia/device/{vid}/cmd",
            {
                "version": SRC_VERSION,
                "sent_ts": int(self._now()),
                "src_id": "simulator",
                "cmd_id": f"sim-{self._cmd_seq}",
                "device_type": device_type,
                "action": action,
                "params": params,
                "reason": reason,
                "priority": "normal",
            },
            retain=False,                  # control.py 주석: retained 금지
        )

    def _next_seq(self, vid: str) -> int:
        self._seq[vid] = self._seq.get(vid, 0) + 1
        return self._seq[vid]

    # ------------------------------------------------------------ 찾기

    def _first_of_type(self, device_type: str) -> str | None:
        for d in self._config.all_devices():
            if d.device_type == device_type and d.enabled:
                return d.id
        return None

    def _sensor_in(self, area: str, stype: str) -> str | None:
        """그 구역의 그 종류 센서. ID 에 묶이지 않으려고 종류로 찾는다."""
        for vid in self._config.sensors_in(area):
            s = self._config.sensor(vid)
            if s is not None and s.type == stype:
                return vid
        return None

    def _sensor_of_type(self, stype: str) -> str | None:
        for s in self._config.all_sensors():
            if s.type == stype and s.enabled:
                return s.id
        return None

    def _sensors_of_type(self, stype: str) -> list[str]:
        return [s.id for s in self._config.all_sensors() if s.type == stype and s.enabled]

    def _spread_max(self) -> float:
        """settled 임계값. 출력에 [정지]/[이동] 을 붙이는 데 쓴다."""
        return float(
            self._config.value("presence", "settle_spread_max_cm", default=40)
        )

    # ------------------------------------------------------------ 입력

    def _read_input(self) -> None:
        """별도 스레드. 상태를 만지지 않고 큐에만 넣는다."""
        for line in sys.stdin:
            self._input.put(line.strip())

    def _drain_input(self) -> None:
        while True:
            try:
                line = self._input.get_nowait()
            except queue.Empty:
                return
            if line:
                self._command(line)

    def _command(self, line: str) -> None:
        parts = line.split()
        verb, args = parts[0].lower(), parts[1:]

        match verb:
            case "quit" | "exit" | "q":
                self.stop()
            case "help" | "?":
                print(HELP)
            case "state":
                self._show()
            case "tv":
                self._device_power("smart_tv", args)
            case "light":
                self._light(args)
            case "lightstate" | "lights":
                self._lightstate(args)
            case "fridge":
                self._fridge(args)
            case "washer":
                self._washer(args)
            case "clean":
                self._clean(args)
            case "dispense":
                vid = self._first_of_type("water_purifier")
                if vid:
                    self._publish_event(
                        vid, "dispensed", water_type="AMBIENT", amount_ml=200
                    )
                    print(f"  {vid} dispensed")
            case "remote":
                vid = self._first_of_type("smart_tv")
                if vid:
                    self._publish_event(vid, "remote_input", button="OK")
                    print(f"  {vid} remote_input")
            case "presence":
                self._presence(args)
            case "motion":
                self._motion(args)
            case "bed":
                self._bed(args)
            case "door":
                self._door(args)
            case "power":
                self._power(args)
            case "climate":
                self._climate(args)
            case "lux":
                self._lux(args)
            case "heartbeat" | "hb":
                self._heartbeat(args)
            case "clock":
                self._clock(args)
            case "idle":
                self._idle()
            case "model":
                self._model(args)
            case "med":
                self._med(args)
            case "weather":
                self._weather(args)
            case "ok":
                # 디스플레이의 확인 버튼. 실제로는 ESP32 버튼이 이 경로다
                self._ack(["SEEN"])
            case "ack":
                self._ack(args)
            case "profile":
                self._profile(args)
            case "clear":
                self._clear_retained()
            case "home":
                self._home()
            case "out":
                self._out()
            case "scenario" | "sc":
                self._scenario(args)
            case "wait":
                self._wait(args)
            case "abort":
                self._script.clear()
                print("  예약 취소")
            case "purifier":
                self._purifier(args)
            case "cooktop":
                self._cooktop(args)
            case _:
                print(f"모르는 명령: {verb} (help)")

    # ------------------------------------------------------------ 시계 명령

    @staticmethod
    def _parse_clock(spec: str, now: float) -> float:
        """'09:00' 이면 now 보다 뒤인 가장 가까운 그 시각.
           '+9m30s' 면 now 에서 더한 시각.

        절대 시각을 '다음 그 시각' 으로 읽으므로 항상 미래로만 간다 —
        22:00 에서 clock 02:00 은 다음날 02:00 이다.
        """
        if spec.startswith("+"):
            return now + _parse_duration(spec[1:])
        if ":" not in spec:
            raise ValueError(f"모르는 시각: {spec}")
        hh, _, mm = spec.partition(":")
        try:
            h, m = int(hh), int(mm)
        except ValueError:
            raise ValueError(f"모르는 시각: {spec}") from None
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError(f"범위를 벗어난 시각: {spec}")
        at = datetime.fromtimestamp(now).replace(
            hour=h, minute=m, second=0, microsecond=0
        )
        if at.timestamp() <= now:
            at += timedelta(days=1)
        return at.timestamp()

    def _clock(self, args: list[str]) -> None:
        """시계를 옮긴다. 엔진과 모니터가 CLOCK_TOPIC 으로 따라온다.

        점프는 '그 시간을 건너뛴다' 는 뜻이다. 건너뛴 구간에 예정돼
        있던 알림은 울리지 않은 것으로 엔진이 처리한다.
        """
        now = self._now()
        if not args:
            print(
                f"  오프셋 {self._offset:+.0f}초 · 지금 "
                f"{datetime.fromtimestamp(now):%m-%d %H:%M:%S}"
            )
            return

        try:
            target = self._parse_clock(args[0], now)
        except ValueError as exc:
            print(f"  {exc}")
            print(USAGE_CLOCK)
            return

        if target <= now:
            print("  시계는 되돌릴 수 없다")
            return

        self._offset += target - now
        self._client.publish(
            CLOCK_TOPIC,
            json.dumps({"offset": self._offset, "at": time.time()}),
            qos=1, retain=True,
        )
        print(
            f"  {datetime.fromtimestamp(now):%H:%M:%S}"
            f" → {datetime.fromtimestamp(target):%H:%M:%S}"
            f"   (오프셋 {self._offset:+.0f}초)"
        )

    def _sync_clock(self, raw: bytes) -> None:
        """엔진·모니터가 들고 있는 오프셋으로 맞춘다.

        재시작하면 _offset 이 0 으로 돌아가는데 CLOCK_TOPIC 은 retained
        라 엔진과 모니터는 옛 오프셋을 유지한다. 그대로 두면 시뮬레이터만
        실시간을 찍고, 다음 clock 명령이 엔진에게 '되돌리기' 로 보여
        DemoClock.jump_to 가 거부한다.

        자기가 쏜 것도 되받지만 같은 값이라 아래 조건에서 걸러진다.
        """
        try:
            offset = float(json.loads(raw)["offset"])
        except Exception:                          # noqa: BLE001
            return
        if offset <= self._offset:
            return
        self._offset = offset
        print(
            f"  시계 동기 — 오프셋 {offset:+.0f}초 "
            f"(지금 {self._now_dt():%m-%d %H:%M:%S})"
        )

    def _idle(self) -> None:
        """모든 구역을 비우고 패드를 푼다. 점프 전에 쓴다.

        문·가전·전력은 건드리지 않는다 — '외출 중 TV 켜둠' 이 의도된
        장면일 수 있다.

        수면 장면에서는 쓰지 않는다. 자는 사람의 정지와 패드는 건너뛴
        시간 내내 참이므로, 씻으면 '침대에서 사라졌다' 가 되어 기상
        판정이 난다.
        """
        areas = 0
        for area in self._config.areas:
            if self._sensor_in(area, "presence") is not None:
                self._presence_at(area, present=False)
                areas += 1
        pads = 0
        for vid in self._sensors_of_type("bed"):
            self._publish_sensor(vid, occupied=False)
            pads += 1
        self._presence_last.clear()
        print(f"  구역 {areas}곳 비움 · 패드 {pads}개 해제")

    # ------------------------------------------------------------ 가전 명령

    def _device_power(self, device_type: str, args: list[str]) -> None:
        vid = self._first_of_type(device_type)
        if vid is None:
            print(f"{device_type} 가 설정에 없음")
            return
        if args and args[0].lower() not in TRUE_WORDS + FALSE_WORDS:
            print(f"사용법: {device_type.split('_')[-1]} <on|off>")
            return
        on = not args or _as_bool(args[0]) is not False
        self._set(vid, power="ON" if on else "OFF")
        print(f"  {vid} power={'ON' if on else 'OFF'}")

    def _light_pick(self, args: list[str]) -> tuple[str | None, list[str]]:
        """[area] 를 벗겨내고 대상 smart_light 를 고른다."""
        area = None
        if args and args[0] in self._config.areas:
            area, args = args[0], args[1:]

        for d in self._config.all_devices():
            if d.device_type == "smart_light" and d.enabled:
                if area is None or d.area == area:
                    return d.id, args

        print(f"smart_light 가 설정에 없음{f' ({area})' if area else ''}")
        return None, args

    @staticmethod
    def _light_params(args: list[str]) -> dict[str, Any] | None:
        if not args or args[0] == "off":
            return {"power": "OFF"}
        if args[0] == "on":
            return {"power": "ON", "brightness": 100}
        try:
            level = int(args[0])
        except ValueError:
            print("사용법: light [area] <0-100|on|off>")
            return None
        return {"power": "ON" if level else "OFF", "brightness": level}

    def _light(self, args: list[str]) -> None:
        """light [area] <0-100|on|off> — 사용자가 손으로 조작한 것처럼.

        state 가 아니라 cmd 를 쏜다. LED 를 ESP32 가 들고 있어서, state 를
        쏘면 브로커만 바뀌고 LED 는 그대로다 — 상황판과 실물이 어긋나고,
        30초 뒤 ESP32 하트비트가 되돌려 놓는다.

        cmd 를 쏘면 ESP32 가 PWM 을 바꾸고 자기 state 를 올린다. 엔진은
        '내가 시키지 않은 state 변화' 로 보므로 수동 조작과 같다 —
        manual_override 와 dim 프로브 응답이 그대로 작동한다.
        """
        vid, args = self._light_pick(args)
        if vid is None:
            return
        params = self._light_params(args)
        if params is None:
            return
        self._publish_cmd(vid, "smart_light", "set", params)
        print(f"  → cmd {vid} " + " ".join(f"{k}={v}" for k, v in params.items()))

    def _lightstate(self, args: list[str]) -> None:
        """lightstate [area] <0-100|on|off> — state 를 직접 쏜다.

        ESP32 없이 맥에서만 돌릴 때 쓴다. 보드가 붙어 있으면 쓰지 말 것 —
        LED 는 안 바뀌고 30초 뒤 하트비트가 되돌린다.
        """
        vid, args = self._light_pick(args)
        if vid is None:
            return
        params = self._light_params(args)
        if params is None:
            return
        self._set(vid, **params)
        print(f"  {vid} " + " ".join(f"{k}={v}" for k, v in params.items()))

    def _fridge(self, args: list[str]) -> None:
        vid = self._first_of_type("smart_fridge")
        if vid is None:
            return
        opened = bool(args) and args[0] == "open"
        now = self._now()
        self._set(vid, door="OPEN" if opened else "CLOSED")
        if opened:
            self._fridge_opened_at = now
            self._publish_event(vid, "door_opened")
        else:
            # 실제로 열려 있던 시간을 보낸다. 고정값이면 '문을 오래 열어둔
            # 이상' 을 테스트할 방법이 없다.
            held = now - self._fridge_opened_at if self._fridge_opened_at else 0.0
            self._fridge_opened_at = None
            self._publish_event(vid, "door_closed", open_duration_sec=round(held, 1))
        print(f"  {vid} door={'OPEN' if opened else 'CLOSED'}")

    def _washer(self, args: list[str]) -> None:
        """washer <cycle>  |  washer done — done 은 완료 이벤트까지 쏜다."""
        vid = self._first_of_type("washer")
        if vid is None:
            return
        what = (args[0] if args else "WASH").upper()
        if what == "DONE":
            self._set(vid, power="ON", cycle="DONE", remain_min=0)
            self._publish_event(vid, "cycle_completed")
            print(f"  {vid} cycle=DONE + cycle_completed")
            return
        self._set(vid, power="ON", cycle=what, remain_min=30)
        print(f"  {vid} cycle={what}")

    def _clean(self, args: list[str]) -> None:
        """clean <start|stop|dock>  |  clean schedule <HH:MM|off>

        예약은 state 의 schedule 필드다. 엔진이 그 값과 현재 시각을
        비교해 스스로 판단한다 — 예약 도달 이벤트는 오지 않는다.
        """
        vid = self._first_of_type("robot_cleaner")
        if vid is None:
            print("robot_cleaner 가 설정에 없음")
            return

        if args and args[0] == "schedule":
            raw = args[1] if len(args) > 1 else "off"
            if raw == "off":
                self._set(vid, schedule=None)
                print(f"  {vid} 예약 없음")
                return
            parts = raw.split(":")
            if len(parts) != 2 or not all(p.isdigit() for p in parts):
                print("사용법: clean schedule <HH:MM|off>")
                return
            self._set(vid, schedule=raw)
            print(f"  {vid} 예약 {raw}")
            return

        what = args[0] if args else "start"
        status = {"start": "CLEANING", "stop": "PAUSED", "dock": "DOCKED"}.get(what)
        if status is None:
            print("사용법: clean <start|stop|dock> | clean schedule <HH:MM|off>")
            return
        self._set(vid, status=status)
        print(f"  {vid} status={status}")

    def _cooktop(self, args: list[str]) -> None:
        """cooktop <cook|off|pause|lock> [화력] [남은분]

        실제 ThinQ 는 화구마다 상태를 주지만, 노드가 '하나라도 COOK 인가'
        로 접어 보낸다. 여기서도 같은 모양으로 쏜다.
        """
        vid = self._first_of_type("cooktop")
        if vid is None:
            print("cooktop 이 설정에 없음")
            return
        what = (args[0] if args else "cook").lower()
        status = {"cook": "COOK", "off": "INITIAL",
                  "pause": "PAUSE", "lock": "LOCK"}.get(what)
        if status is None:
            print("사용법: cooktop <cook|off|pause|lock> [화력] [남은분]")
            return

        if status == "COOK":
            self._set(
                vid, status=status, zone="LEFT_FRONT",
                power_level=int(args[1]) if len(args) > 1 else 7,
                remain_min=int(args[2]) if len(args) > 2 else None,
            )
            print(f"  {vid} COOK 화력 {self.devices[vid]['power_level']}")
            return

        self._set(vid, status=status, zone=None,
                  power_level=None, remain_min=None)
        print(f"  {vid} {status}")

    # ------------------------------------------------------------ 센서 명령

    def _emit_presence(self, vid: str, fields: dict[str, Any]) -> None:
        """presence 한 발을 쏘고 하트비트용으로 기억한다.

        손으로 쏜 직후에는 하트비트를 미룬다 — 펌웨어도 changed 와
        PERIOD_MS 중 먼저 오는 쪽으로 발행한다.

        센서별로 마지막 값 하나만 들고 있다. 배타성 쓸이가 다른 구역을
        false 로 덮으면 그 값이 기억되므로, 하트비트가 옛 재실을
        되살리지 않는다.
        """
        self._presence_last[vid] = fields
        self._hb_next = time.time() + self._hb_period
        self._publish_sensor(vid, **fields)

    def _presence_at(
        self,
        area: str,
        *,
        present: bool = True,
        target: str = "BOTH",
        spread_cm: int = 90,
        distance_cm: int = 150,
        exclusive: bool = True,
    ) -> str | None:
        """mmWave 한 발. 매크로와 입력 명령이 함께 쓰는 내부 경로다.

        energy 는 present 면 100 으로 고정한다. 실물에서 energy 는
        max(이동, 정지) 라서 앉아 있어도 100 이고(2026-10-16 실측),
        엔진의 판정에서는 쓰이지 않는다 — factors 기록용만 남았다.
        """
        vid = self._sensor_in(area, "presence")
        if vid is None:
            print(f"{area} 에 presence 센서가 없음")
            return None

        if present and exclusive:
            for other in self._config.areas:
                if other == area:
                    continue
                ovid = self._sensor_in(other, "presence")
                if ovid is not None:
                    self._emit_presence(ovid, {
                        "present": False, "confidence": "high",
                        "energy": 0, "distance_cm": 0,
                        "target": "NONE", "dist_spread_cm": 0,
                    })

        self._emit_presence(vid, {
            "present": present,
            "confidence": "high",
            "energy": 100 if present else 0,
            "distance_cm": distance_cm if present else 0,
            "target": target if present else "NONE",
            "dist_spread_cm": spread_cm if present else 0,
        })
        return vid

    def _presence(self, args: list[str]) -> None:
        """presence <area> <true|false> [target] [sp] [d=N] [keep]

        present 가 참이면 target 과 sp 를 반드시 적는다. 시뮬레이터가
        대신 고르지 않는다 — 옛 'still' 이 energy=3 을 끼워 넣어서
        맥에서만 EATING 이 뜨던 일이 있었다 (2026-10-16).

        부재면 뒤를 적지 않는다. 펌웨어도 부재면 target=NONE,
        dist_spread_cm=0 을 보낸다 (ld2410.cpp 의 'dist = next ? ... : 0').
        """
        if len(args) < 2:
            print(USAGE_PRESENCE)
            return

        area = args[0]
        pos: list[str] = []
        flags: list[str] = []
        for a in args[1:]:
            (flags if ("=" in a or a.lower() == "keep") else pos).append(a)

        present = _as_bool(pos[0]) if pos else None
        if present is None:
            print(USAGE_PRESENCE)
            return

        keep = any(f.lower() == "keep" for f in flags)
        distance = 150
        for f in flags:
            if "=" not in f:
                continue
            key, value = f.split("=", 1)
            if key.lower() not in ("d", "dist", "distance"):
                print(f"모르는 옵션: {f}")
                return
            try:
                distance = int(value)
            except ValueError:
                print(f"거리가 숫자가 아님: {f}")
                return

        if not present:
            if len(pos) > 1:
                print("부재에는 target·sp 를 적지 않는다 — NONE / 0 으로 나간다")
                return
            vid = self._presence_at(area, present=False, exclusive=not keep)
            if vid is not None:
                print(f"  {vid} present=false target=NONE sp=0")
            return

        if len(pos) < 3:
            print(USAGE_PRESENCE)
            return

        target = pos[1].upper()
        if target not in TARGETS:
            print(f"target 은 {' | '.join(TARGETS)}")
            return
        try:
            spread = int(pos[2])
        except ValueError:
            print("sp (dist_spread_cm) 가 숫자가 아님")
            return

        vid = self._presence_at(
            area, present=True, exclusive=not keep,
            target=target, spread_cm=spread, distance_cm=distance,
        )
        if vid is None:
            return

        mark = "정지" if spread < self._spread_max() else "이동"
        note = "  (다른 구역 유지)" if keep else ""
        print(
            f"  {vid} present=true target={target} sp={spread} d={distance} "
            f"[{mark}]{note}"
        )

    def _motion(self, args: list[str]) -> None:
        """motion <area> <true|false> — PIR. presence 와 다른 경로를 탄다."""
        if len(args) < 2:
            print("사용법: motion <area> <true|false>")
            return
        area = args[0]
        vid = self._sensor_in(area, "motion")
        if vid is None:
            print(f"{area} 에 motion 센서가 없음")
            return
        on = _as_bool(args[1])
        if on is None:
            print("사용법: motion <area> <true|false>")
            return
        self._publish_sensor(vid, motion=on, confidence="low")
        print(f"  {vid} motion={str(on).lower()}")

    def _bed(self, args: list[str]) -> None:
        """bed [area] <true|false> — 압력 패드. 패드가 하나면 area 생략."""
        if not args:
            print("사용법: bed [area] <true|false>")
            return

        area = None
        if args[0] in self._config.areas:
            area, args = args[0], args[1:]
        if not args:
            print("사용법: bed [area] <true|false>")
            return

        if area is None:
            pads = self._sensors_of_type("bed")
            if len(pads) > 1:
                where = " | ".join(self._config.sensor(v).area for v in pads)
                print(f"  패드가 여럿이다 — 구역을 적어라: bed <{where}> <true|false>")
                return

        vid = self._sensor_in(area, "bed") if area else self._sensor_of_type("bed")
        if vid is None:
            print(f"bed 센서가 없음{f' ({area})' if area else ''}")
            return

        occupied = _as_bool(args[0])
        if occupied is None:
            print("사용법: bed [area] <true|false>")
            return
        self._publish_sensor(vid, occupied=occupied)
        print(
            f"  {vid} ({self._config.sensor(vid).area}) "
            f"occupied={str(occupied).lower()}"
        )

    def _door(self, args: list[str]) -> None:
        """door <true|false> — 현관문. true = 열림."""
        vid = self._sensor_of_type("door")
        if vid is None:
            print("door 센서가 없음")
            return
        if not args:
            print("사용법: door <true|false>")
            return
        opened = _as_bool(args[0])
        if opened is None:
            print("사용법: door <true|false>")
            return
        self._publish_sensor(vid, open=opened)
        print(f"  {vid} open={str(opened).lower()}")

    def _power(self, args: list[str]) -> None:
        if len(args) < 2:
            print("사용법: power <vid> <watt>")
            return
        vid = args[0]
        try:
            watt = float(args[1])
        except ValueError:
            print("watt 가 숫자가 아님")
            return
        # state 를 채워 보낸다 — 노드가 채우면 엔진이 그대로 쓴다 (명세).
        # 히스테리시스를 거치려면 state 를 빼면 된다.
        state = "ON" if watt >= 300 else ("STANDBY" if watt > 10 else "OFF")
        self._publish_sensor(vid, watt=watt, state=state)
        print(f"  {vid} watt={watt} state={state}")

    def _climate(self, args: list[str]) -> None:
        """climate <온도> <습도> — HEAT_ONSET / DRY_ONSET 용."""
        vid = self._sensor_of_type("climate")
        if vid is None:
            print("climate 센서가 없음")
            return
        temp = float(args[0]) if args else 24.0
        hum = float(args[1]) if len(args) > 1 else 48.0
        self._publish_sensor(vid, temperature_c=temp, humidity_pct=hum)
        print(f"  {vid} {temp}°C {hum}%")

    def _purifier(self, args: list[str]) -> None:
        """purifier <온도> <습도> [pm25] — 공기청정기가 재는 실내값.

        climate 센서(DHT)보다 우선한다. 둘 다 쏴서 어느 쪽이 쓰이는지
        확인할 수 있다.
        """
        vid = self._first_of_type("air_purifier")
        if vid is None:
            print("air_purifier 가 설정에 없음")
            return
        temp = float(args[0]) if args else 24.0
        hum = float(args[1]) if len(args) > 1 else 48.0
        pm = int(args[2]) if len(args) > 2 else 15
        self._set(vid, power="ON", mode="AUTO",
                  temperature_c=temp, humidity_pct=hum, pm25=pm)
        print(f"  {vid} {temp}°C {hum}% pm25={pm}")

    def _lux(self, args: list[str]) -> None:
        """lux <값> — 조명을 뺀 바닥 조도. 발행값은 조명 기여를 더한 것이다."""
        if args and args[0].lower() == "off":
            self._lux_owned = False
            print("  조도 발행 중지 — 실물 센서가 맡는다")
            return
        if args and args[0].lower() == "on":
            self._lux_owned = True
            print("  조도 발행 재개")
            self._publish_lux()
            return

        if self._sensor_of_type("light") is None:
            print("light 센서가 없음")
            return
        self._lux_base = float(args[0]) if args else 100.0
        self._publish_lux()

    def _publish_lux(self) -> None:
        """바닥 조도 + 같은 구역에서 켜져 있는 조명의 기여를 발행한다.

        실물에서는 조명을 올리면 조도가 따라 올라 DAY_LIGHT 가 데드밴드에
        들어가 멈춘다. 그 고리가 없으면 밝기 100 까지 올라간다.
        """
        vid = self._sensor_of_type("light")
        if vid is None:
            return
        s = self._config.sensor(vid)
        added = 0.0
        for d in self._config.all_devices():
            if d.device_type != "smart_light" or d.area != s.area:
                continue
            st = self.devices.get(d.id, {})
            if st.get("power") != "ON":
                continue
            added += float(st.get("brightness", 0)) * self.LUX_PER_BRIGHTNESS
        total = round(self._lux_base + added, 1)
        self._publish_sensor(vid, illuminance_lux=total)
        print(f"  {vid} {total} lux (바닥 {self._lux_base:g} + 조명 {added:g})")

    # ------------------------------------------------------------ 모델·복약

    def _model(self, args: list[str]) -> None:
        """model [late|normal] — KDE 모델을 쏜다.

        peaks 가 없으면 MEAL_PROMPT 도 식후 복약도 통째로 안 돈다.
        배치(RPi4)가 보내는 것이지만 테스트에서는 여기서 만든다.

            model          지금이 봉우리 한가운데 → TIME_NORMAL
            model late     봉우리를 지남 → ANOMALY (식사 알림)

        '지금' 은 가상 시각이다. clock 으로 19:00 에 서 있으면 봉우리도
        거기 선다 — 안 그러면 점프한 시각과 봉우리가 어긋난다.
        """
        late = bool(args) and args[0] == "late"
        now = self._now_dt()
        m = now.hour * 60 + now.minute

        if late:
            # 봉우리는 2시간 전. 지금은 구간 꼬리라 tail 이 바닥이다.
            center, start, end = m - 120, m - 180, m + 60
        else:
            # 지금이 봉우리 중심. 뒤에 질량이 절반 남아 TIME_NORMAL.
            center, start, end = m, m - 120, m + 120

        # 두 번째 봉우리 — 겹치지 않게 멀리 둔다
        c2, s2, e2 = m + 360, m + 300, m + 420

        density = [0.0] * GRID_N
        self._bump(density, center, weight=0.6)
        self._bump(density, c2, weight=0.4)
        total = sum(density)
        density = [d / total for d in density]

        payload = {
            "version": SRC_VERSION,
            "sent_ts": int(self._now()),
            "src_id": "rpi4",
            "trained_at": int(self._now()) - 3600,
            "sample_days": 21,
            "distributions": {
                "meal_time": {
                    "grid_min": 0,
                    "grid_step": GRID_STEP,
                    "density": density,
                    "peaks": [
                        self._peak(center, start, end),
                        self._peak(c2, s2, e2),
                    ],
                },
            },
            "predictability": {"meal_time": 0.41},
        }
        self._publish_raw("hestia/model/kde", payload, retain=True)

        def hm(x: int) -> str:
            x %= 1440
            return f"{x // 60}:{x % 60:02d}"

        print(
            f"  kde 발행 — 봉우리 {hm(center)} [{hm(start)}~{hm(end)}),"
            f" {hm(c2)} [{hm(s2)}~{hm(e2)})"
            f"  {'(지금은 꼬리)' if late else '(지금은 중심)'}"
        )

    @staticmethod
    def _peak(center: int, start: int, end: int) -> dict[str, Any]:
        return {
            "center": center % 1440,
            "from": start % 1440,
            "to": end % 1440,
            "predictability": 0.54,
            "days_ratio": 1.0,
            "meals_per_day": 1.0,
        }

    @staticmethod
    def _bump(density: list[float], center_min: int, *, weight: float) -> None:
        """center 주변 ±30분에 질량을 모은다."""
        c = (center_min % 1440) // GRID_STEP
        for off, share in ((0, 0.4), (-1, 0.2), (1, 0.2), (-2, 0.1), (2, 0.1)):
            density[(c + off) % GRID_N] += weight * share

    def _med(self, args: list[str]) -> None:
        """med [clear] — 복약 등록을 쏜다.

        감기약 = 식후 30분
        혈압약 = 08:00 고정

        고정 시각은 등록보다 뒤여야 한다. 엔진이 '오늘 등록한 약이면
        등록 시각 이전의 분량은 건너뛴다' 로 처리하므로
        (scenarios._fixed_medication 의 reg_min), 08:00 을 보려면
        clock 으로 08시 전에 서 있을 때 med 를 쳐야 한다.
        """
        if args and args[0] == "clear":
            payload = {
                "version": SRC_VERSION, "sent_ts": int(self._now()),
                "src_id": "rpi5-api", "medications": [],
            }
            self._publish_raw("hestia/registry/medications", payload, retain=True)
            print("  복약 등록 비움")
            return

        now = self._now_dt()
        today = now.date()
        end = today + timedelta(days=4)

        payload = {
            "version": SRC_VERSION,
            "sent_ts": int(self._now()),
            "src_id": "rpi5-api",
            "medications": [
                {
                    "id": "med-test-cold",
                    "name": "감기약",
                    "schedule": {"type": "after_meal", "delay_min": 30},
                    "start_date": today.isoformat(),
                    "days": 5,
                    "end_date": end.isoformat(),
                    "refill_notice": True,
                },
                {
                    "id": "med-test-bp",
                    "name": "혈압약",
                    "schedule": {"type": "fixed", "times": ["08:00"]},
                    "start_date": today.isoformat(),
                    "days": 30,
                    "end_date": (today + timedelta(days=29)).isoformat(),
                    "refill_notice": False,
                },
            ],
        }
        self._publish_raw("hestia/registry/medications", payload, retain=True)
        print("  복약 등록 — 감기약(식후 30분), 혈압약(08:00 고정)")

    def _weather(self, args: list[str]) -> None:
        """weather [on|off|dry] — 특보를 쏜다.

        on   폭염 특보 → HEAT_ADVISORY 로 수분 알림
        dry  건조 특보
        off  특보 해제 (관측값만)

        observed_at 을 지금으로 넣는 것이 중요하다 — 엔진이
        age_sec <= weather.max_age_sec 를 보고 오래된 값을 버린다.
        """
        what = args[0].lower() if args else "on"
        now = int(self._now())

        if what == "dry":
            warnings = [{"name": "건조주의보", "type": "DRY", "level": "ADVISORY"}]
            temp, hum = 18.0, 22.0
        elif what == "off":
            warnings = []
            temp, hum = 22.0, 48.0
        else:
            warnings = [{"name": "폭염주의보", "type": "HEAT", "level": "ADVISORY"}]
            temp, hum = 33.1, 62.0

        payload = {
            "version": SRC_VERSION,
            "sent_ts": now,
            "src_id": "rpi4",
            "source": "kma",
            "observed_at": now,
            "temperature_c": temp,
            "humidity_pct": hum,
            "precip_1h_mm": 0.0,
            "precip_type": "NONE",
            "wind_speed_ms": 1.4,
            "warnings": warnings,
            "warnings_issued_at": now,
        }
        # 1시간 주기 관측값이므로 retained — 엔진이 재시작해도 현재 날씨를 안다
        self._publish_raw("hestia/external/weather", payload, retain=True)
        label = ", ".join(w["type"] for w in warnings) or "특보 없음"
        print(f"  날씨 {temp}°C {hum}% — {label}")

    def _ack(self, args: list[str]) -> None:
        """ack [SEEN|DELIVERED] — 마지막 알림에 응답한다."""
        if self._last_notify_id is None:
            print("아직 받은 알림이 없음")
            return
        kind = args[0].upper() if args else "SEEN"
        vid = self._first_of_type("display_node") or "vd-10"
        payload = {
            "version": SRC_VERSION,
            "sent_ts": int(self._now()),
            "src_id": vid,
            "seq": self._next_seq(vid),
            "notify_id": self._last_notify_id,
            "ack_type": kind,
        }
        self._publish_raw("hestia/notify/ack", payload, retain=False)
        print(f"  ack {kind} → {self._last_notify_id}")

    def _profile(self, args: list[str]) -> None:
        name = args[0].upper() if args else "DEMO"
        payload = {
            "version": SRC_VERSION, "sent_ts": int(self._now()),
            "src_id": "rpi4", "profile": name,
        }
        self._publish_raw("hestia/system/profile", payload, retain=True)
        print(f"  프로파일 {name}")

    def _clear_retained(self) -> None:
        """retained 를 전부 지운다. 스키마를 바꿨을 때 옛 값이 남는다.

        시계 오프셋(CLOCK_TOPIC)은 지우지 않는다 — 지우면 늦게 붙는
        모니터가 혼자 실시간을 찍는다.
        """
        topics = ["hestia/model/kde", "hestia/registry/medications",
                  "hestia/external/weather", "hestia/system/profile"]
        for s in self._config.all_sensors():
            topics.append(f"hestia/sensor/{s.id}/state")
        for d in self._config.all_devices():
            topics.append(f"hestia/device/{d.id}/state")
        for t in topics:
            self._client.publish(t, b"", qos=1, retain=True)

        # 화면과 기억을 같이 비운다. _last_context 를 안 비우면 엔진이
        # 초기값을 다시 발행해도 '안 바뀌었다' 로 걸러져 안 찍힌다.
        self._last_context.clear()
        self._last_notify_id = None
        self._presence_last.clear()
        print("\033[2J\033[H", end="")
        self._client.publish("hestia/sim/clear",
                             json.dumps({"at": self._now()}),
                             qos=1, retain=False)
        print(f"  retained {len(topics)}건 삭제 — 화면 초기화")

        # 가전 상태는 바로 다시 쏜다. 지우고 두면 엔진이 전원을 '모른다' 로
        # 보고, _powered() 가 꺼진 TV 를 켜진 것으로 취급한다 (2026-10-17).
        for vid in self.devices:
            self._publish_state(vid)
        print(f"  가전 {len(self.devices)}대 상태 재발행")

    # ------------------------------------------------------------ 묶음 명령

    def _home(self) -> None:
        """집에 있는 상태로 되돌린다 — 거실 재실(걷는 중), 나머지 비움."""
        if self._presence_at("living", target="BOTH", spread_cm=90) is None:
            return
        print("  거실 재실 (BOTH sp=90), 나머지 구역 비움")

    def _out(self) -> None:
        """전 구역을 비운다. 현관문까지 여닫아 door_exit 근거를 만든다."""
        for area in self._config.areas:
            if self._sensor_in(area, "presence") is not None:
                self._presence_at(area, present=False)
        for vid in self._sensors_of_type("bed"):
            self._publish_sensor(vid, occupied=False)

        door = self._sensor_of_type("door")
        if door is not None:
            self._publish_sensor(door, open=True)
            self._after(1, "현관문 닫힘", lambda: self._publish_sensor(door, open=False))
        print("  전 구역 비움 + 현관 개폐 — door_exit_quiet_sec 뒤 AWAY")

    # ------------------------------------------------------------ 시나리오

    def _after(self, delay: float, label: str, fn: Callable[[], None]) -> None:
        self._script.append((time.time() + delay, label, fn))

    def _run_script(self) -> None:
        if not self._script:
            return
        now = time.time()
        due = [s for s in self._script if s[0] <= now]
        self._script = [s for s in self._script if s[0] > now]
        for _, label, fn in due:
            if label:
                print(f"  ▶ {label}")
            fn()

    def _wait(self, args: list[str]) -> None:
        sec = float(args[0]) if args else 10
        self._after(sec, f"{sec:.0f}초 경과", lambda: None)
        print(f"  {sec:.0f}초 뒤 표시")

    def _scenario(self, args: list[str]) -> None:
        if not args:
            print(SCENARIOS)
            return
        name = args[0].lower()
        runner = {
            "meal": self._sc_meal,
            "sleep": self._sc_sleep,
            "hydration": self._sc_hydration,
            "away": self._sc_away,
            "safety": self._sc_safety,
        }.get(name)
        if runner is None:
            print(SCENARIOS)
            return
        self._script.clear()
        print(f"\n=== 시나리오 {name} ===")
        runner()

    def _sc_meal(self) -> None:
        """조리 → 식사 → 이탈 → 묶음 종료 → 식후 복약.

        config-test 기준: close_grace 20초, min_duration 15초.

        식사 자세를 BOTH sp=30 으로 쏜다. 실물에서 식사는 팔이 움직여
        target 이 BOTH 이고, 자리를 안 옮겨 sp 가 작다 — STATIC 으로
        쏘면 실물과 다른 것을 확인하게 된다 (2026-10-16).
        """
        if self._sensor_in("kitchen", "presence") is None:
            print("주방 presence 센서가 없음")
            return
        induction = next(
            (s.id for s in self._config.all_sensors()
             if s.type == "power" and "MEAL" in s.roles), None
        )
        if induction is None:
            print("인덕션 전력 센서가 없음 (role=MEAL)")
            return

        # 주방에 들어가면 나머지 구역은 비워진다 (_presence_at 이 한다).
        # 그러지 않으면 거실이 true 로 남아 엔진이 두 명으로 센다.
        def pres(target: str, spread: int) -> Callable[[], None]:
            return lambda: self._presence_at(
                "kitchen", target=target, spread_cm=spread
            )

        self._after(0, "주방 입장 (BOTH sp=90)", pres("BOTH", 90))
        self._after(2, "인덕션 ON → COOKING", lambda: self._publish_sensor(
            induction, watt=1200.0, state="ON"))
        self._after(20, "인덕션 OFF", lambda: self._publish_sensor(
            induction, watt=2.0, state="OFF"))
        self._after(23, "냉장고 열고 닫기", self._fridge_cycle)
        self._after(26, "앉아서 식사 (BOTH sp=30) → EATING", pres("BOTH", 30))
        self._after(50, "주방 이탈", lambda: self._presence_at(
            "kitchen", present=False))
        self._after(52, "거실로", self._home)
        self._after(
            80,
            "묶음 종료 대기 — grace 뒤 log/t0, 다시 1분 뒤 복약 알림",
            lambda: None,
        )

    def _fridge_cycle(self) -> None:
        """열고 2초 뒤 닫는다. 개폐 시간은 _fridge 가 실제로 잰다."""
        self._fridge(["open"])
        self._after(2, "", lambda: self._fridge(["close"]))

    def _sc_sleep(self) -> None:
        """침실 재실 → 정지 → 프로브 → 확정.

        config-test 기준: probe_still 30초, no_channel 60초, timeout 10초.
        침실 조명을 켜두면 dim 프로브, 꺼두면 긴 정지만으로 확정한다.

        프로브에 "깨어 있다"고 답하려면 'bed bedroom false' 다.
        움직임으로는 안 깨진다 (scenarios._responded, 2026-10-16).
        """
        if self._sensor_in("bedroom", "presence") is None:
            print("침실 presence 센서가 없음")
            return
        pad = self._sensor_in("bedroom", "bed")

        # 입장이 나머지 구역을 비운다 — 따로 '거실 비움' 단계가 필요 없다
        self._after(0, "침실 입장 (BOTH sp=90)", lambda: self._presence_at(
            "bedroom", distance_cm=120, target="BOTH", spread_cm=90))
        if pad:
            self._after(3, "침대 점유", lambda: self._publish_sensor(pad, occupied=True))
        self._after(5, "정지 시작 (STATIC sp=3)", lambda: self._presence_at(
            "bedroom", distance_cm=120, target="STATIC", spread_cm=3))
        self._after(
            45,
            "프로브 발동 대기 — 조명이 켜져 있으면 dim, 아니면 60초 정지로 확정",
            lambda: None,
        )

    def _sc_hydration(self) -> None:
        """급수 → 간격 경과 → 알림.

        config-test 기준: gap_sec 60초.
        """
        self._after(0, "집에 있음", self._home)
        self._after(2, "급수 — 여기서 간격이 시작된다", lambda: self._dispense())
        self._after(70, "간격 60초 경과 — LONG_GAP 알림이 나와야 한다", lambda: None)

    def _dispense(self) -> None:
        vid = self._first_of_type("water_purifier")
        if vid:
            self._publish_event(vid, "dispensed", water_type="AMBIENT", amount_ml=200)

    def _sc_away(self) -> None:
        """외출 → AWAY 확정 → 귀가 → 10분(테스트 20초) 뒤 수분 알림.

        config-test 기준: door_exit_quiet 20초, return_min_away 30초,
        return_delay 20초.
        """
        self._after(0, "외출 — 전 구역 비움 + 현관 개폐", self._out)
        self._after(30, "AWAY 확정됐는지 확인 (context/away 를 보세요)", lambda: None)
        self._after(45, "귀가 — 현관 개폐", self._come_back)
        self._after(48, "거실 재실", self._home)
        self._after(75, "귀가 20초 뒤 RETURNED_HOME 알림", lambda: None)

    def _come_back(self) -> None:
        door = self._sensor_of_type("door")
        if door is None:
            return
        self._publish_sensor(door, open=True)
        self._after(1, "", lambda: self._publish_sensor(door, open=False))

    def _sc_safety(self) -> None:
        """화장실 재실 → 정지 → 낙상 판정.

        config-test 기준: still_sec 30초.

        알림에 "괜찮다"고 답하려면 움직임을 쏜다 —
        'presence bathroom true BOTH 90'. 발송 뒤에 시작된 이동 성분만
        응답으로 인정된다 (notify._comply_movement, 2026-10-16).
        """
        if self._sensor_in("bathroom", "presence") is None:
            print("화장실 presence 센서가 없음")
            return
        self._after(0, "화장실 입장 (BOTH sp=90)", lambda: self._presence_at(
            "bathroom", distance_cm=100, target="BOTH", spread_cm=90))
        self._after(3, "정지 (STATIC sp=2)", lambda: self._presence_at(
            "bathroom", distance_cm=100, target="STATIC", spread_cm=2))
        self._after(40, "30초 정지 — SAFETY 알림이 나와야 한다", lambda: None)

    # ------------------------------------------------------------ 출력

    def _show(self) -> None:
        for vid in sorted(self.devices):
            d = self._config.device(vid)
            fields = " ".join(f"{k}={v}" for k, v in self.devices[vid].items())
            print(f"  {vid:8} {d.device_type:16} {fields}")
        if self._offset:
            print(
                f"  가상 시각 {self._now_dt():%m-%d %H:%M:%S}"
                f"  (오프셋 {self._offset:+.0f}초)"
            )
        if self._pending:
            print(f"  전환 대기 {len(self._pending)}건")
        if self._script:
            print(f"  예약 단계 {len(self._script)}건")
        if self._last_notify_id:
            print(f"  마지막 알림 {self._last_notify_id}")


SCENARIOS = """
scenario <이름> — 단계를 시간 간격으로 자동 실행 (abort 로 취소)

  meal       조리 → 식사(BOTH sp=30) → 이탈 → 묶음 종료 → 식후 복약
             먼저 'model' 과 'med' 를 쏴야 복약 알림이 나온다
  sleep      침실 재실 → 정지 → 프로브 → 수면 확정
  hydration  급수 → 간격 경과 → LONG_GAP 알림
  away       외출 → AWAY → 귀가 → RETURNED_HOME 알림
  safety     화장실 재실 → 정지 → 낙상 알림

재실은 배타적이다 — 어느 구역에 들어가면 나머지는 비워진다.
"""

HELP = """
가전
  tv [on|off]              TV 전원
  light [area] <0-100|on|off>    cmd 로 쏜다 — ESP32 의 LED 가 같이 바뀐다
  lightstate [area] <0-100|...>  state 를 직접 쏜다. 보드 없이 테스트할 때만
  fridge [open|close]      냉장고 문
  washer [WASH|RINSE|SPIN|DONE]
  clean <start|stop|dock>  로봇 청소기
  dispense                 정수기 급수 이벤트
  remote                   TV 리모컨 입력 이벤트

센서 — 센서가 보내는 값을 그대로 적는다. 시뮬레이터가 대신 고르지 않는다.
  presence <area> <true|false> [target] [sp] [d=N] [keep]
      present 가 true 면 target 과 sp 를 반드시 적는다
        target   NONE | MOVING | STATIC | BOTH
        sp       dist_spread_cm — 최근 10초 거리 변동폭
        d=N      distance_cm (기본 150)
        keep     다른 구역을 비우지 않는다 — 2인 상황 테스트용
      presence kitchen true BOTH 90     걸어 들어옴
      presence kitchen true BOTH 30     앉아서 식사 — 팔은 움직이고 자리는 그대로
      presence bedroom true STATIC 3    완전 정지 — 자는 사람·쓰러진 사람
      presence kitchen false            이탈 (NONE / 0 으로 나간다)
  motion <area> <true|false>   PIR
  bed [area] <true|false>      압력 패드 (패드가 하나면 area 생략)
  door <true|false>            현관문 — true = 열림
  power <vid> <watt>           전력 센서
  climate <온도> <습도>         온습도 — HEAT_ONSET / DRY_ONSET
  lux <값>                     조도
  heartbeat [초|off]           presence 재발행 주기 (기본 20초)
      실제 노드의 PERIOD_MS 와 같다. 끄면 손으로 쏠 때만 엔진이
      깨어나므로 dwell·still_sec 가 시각이 지나도 안 걸린다

  settled 는 sp < settle_spread_max_cm (기본 40) 이다. 실측 기준:
  앉음 ≤32 / 걷기 ≥45 (2026-10-16). 출력의 [정지]/[이동] 이 그 판정이다.

시각 — 하루를 몇 장면으로 보여줄 때 쓴다
  clock 09:00              다음 09:00 으로 점프. 항상 미래로만 간다
  clock +9m30s             상대 점프 (+4h, +570 도 된다)
  clock                    현재 오프셋과 가상 시각
  idle                     전 구역 비움 + 패드 해제 — 점프 전에 친다

  점프는 그 시간을 건너뛴다는 뜻이다. 건너뛴 구간에 예정돼 있던 알림은
  울리지 않은 것으로 엔진이 처리한다.

  점프하면 안 되는 때 — 프로브가 떠 있을 때, EATING 판정 중,
  확인이 필요한 알림이 떠 있을 때. 절차가 한 틱에 지나가 버린다.

  수면 장면에서는 idle 을 치지 않는다. 자는 사람의 정지와 패드는
  건너뛴 시간 내내 참이다.

배치·앱이 보내는 것
  model [late]             KDE 모델. 없으면 식사·복약이 안 돈다
                             (기본) 지금이 봉우리 중심 → TIME_NORMAL
                             late   봉우리를 지남 → 식사 알림
  med [clear]              복약 등록 (감기약 식후 1분 / 혈압약 2분 뒤)
  weather [on|off|dry]     특보. on=폭염 → HEAT_ADVISORY 수분 알림
  profile [REAL|DEMO]      프로파일 전환
  ok                       확인 버튼 (= ack SEEN)
  ack [SEEN|DELIVERED]     마지막 알림에 응답

수면 프로브에 "깨어 있다"고 답하는 길은 셋이다 (scenarios._responded)
  ok                       버튼 — 배너 프로브처럼 알림이 있을 때만
  light <구역> <밝기>       조명을 되돌림 — dim 프로브 (cmd 경로다. ESP32 가 꺼져 있으면 안 먹는다)
  bed <구역> false          패드 해제 — 일어나 나간 것
      움직임(presence)으로는 안 깨진다. target 에 크기가 없어
      뒤척임과 깨어서 손을 쓰는 것이 구별되지 않기 때문 (2026-10-16)

낙상 알림에 "괜찮다"고 답하는 길
  presence <구역> true BOTH 90   발송 뒤에 시작된 이동 성분만 인정된다

묶음
  home                     거실 재실(BOTH sp=90), 나머지 비움
  out                      전 구역 비움 + 현관 개폐 → AWAY
  scenario <이름>           시나리오 자동 실행 (scenario 만 치면 목록)
  wait <초>                그만큼 뒤에 표시
  abort                    예약된 단계 취소

기타
  state                    현재 가전 상태
  clear                    retained 전부 삭제
  help / quit
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HESTIA 가전·센서 시뮬레이터")
    parser.add_argument(
        "--config", default=os.environ.get("HESTIA_CONFIG", "./config"),
        help="homes/ 와 policy.toml 이 있는 디렉터리",
    )
    parser.add_argument("--home", default=os.environ.get("HESTIA_HOME", "demo"))
    parser.add_argument("--host", default=os.environ.get("MQTT_HOST", "localhost"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883"))
    )
    parser.add_argument(
        "--no-watch", action="store_true",
        help="엔진이 내보내는 것을 찍지 않는다",
    )
    args = parser.parse_args(argv)

    root = Path(args.config)
    try:
        config = load(root / "homes" / f"{args.home}.toml", root / "policy.toml")
    except ConfigError as exc:
        print(f"설정 오류: {exc}")
        return 1

    sim = Simulator(config, args.host, args.port, watch=not args.no_watch)
    try:
        sim.run()
    except KeyboardInterrupt:
        sim.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())