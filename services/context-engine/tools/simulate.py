"""가전·센서 시뮬레이터.

엔진을 실기기 없이 돌려보기 위한 도구다. 패키지 밖에 두는 것은
엔진의 일부가 아니라 엔진을 테스트하는 물건이기 때문이다.

    cmd 수신 → 내부 상태 변경 → state 재발행 → 엔진이 결과를 본다

양방향인 것이 핵심이다. mosquitto_pub 으로는 state 를 쏠 수 있지만
cmd 를 받을 수 없어서, 제어가 먹혔는지 확인할 방법이 없다.

엔진이 내보내는 것(context, 알림, 판정, t0 로그)도 함께 구독해 찍는다 —
쏘기만 하고 결과를 못 보면 터미널 두 개를 번갈아 봐야 한다.

재실은 배타적이다. 사람은 한 명이고 한 곳에만 있으므로, 어느 구역을
켜면 나머지 구역을 비운다 — 실제 mmWave 도 사람이 나가면 스스로
present=false 를 올린다. 두 명을 만들어 보려면 'presence <area> keep'.

점진 변화는 중간값을 보내지 않는다 — transition_ms 만큼 기다렸다가
최종값 하나만 발행한다. 실제 기기도 대개 그렇고, 중간값이 오면
엔진의 되돌림 판정이 그것을 사용자 조작으로 오인한다.

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
from datetime import date, datetime, timedelta
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
}
DEFAULT_INITIAL = {"power": "OFF"}

# action 별로 바뀌는 필드 (명세). set 은 params 를 그대로 병합한다.
ACTION_EFFECT = {
    "start": {"status": "CLEANING"},
    "stop": {"status": "PAUSED"},
    "dock": {"status": "DOCKED"},
}

TRANSITION_KEY = "transition_ms"

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


class Simulator:
    """가전 상태를 들고 cmd 에 반응한다. 센서는 손으로 쏜다."""

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
        # (적용할 시각, vid, params) — transition_ms 를 블로킹 없이 기다린다
        self._pending: list[tuple[float, str, dict[str, Any]]] = []
        # (실행할 시각, 설명, 함수) — 시나리오 매크로의 단계들
        self._script: list[tuple[float, str, Callable[[], None]]] = []
        self._input: queue.Queue[str] = queue.Queue()

        self._last_notify_id: str | None = None
        self._last_context: dict[str, str] = {}

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"hestia-sim-{os.getpid()}"
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

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

        print(f"브로커 {self._host}:{self._port} — help 로 명령 목록")
        while self._running:
            self._client.loop(timeout=0.1)
            self._apply_due()
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

        if topic.endswith("/cmd"):
            try:
                cmd = parse(topic, msg.payload, time.time())
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

        stamp = datetime.now().strftime("%H:%M:%S")

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
                    f"med={len(p.get('medications', []))}"
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

    # ------------------------------------------------------------ 발행

    def _set(self, vid: str, **changes: Any) -> None:
        """내부 상태를 바꾸고 state 를 발행한다."""
        self.devices.setdefault(vid, {}).update(changes)
        self._publish_state(vid)

    def _publish_state(self, vid: str) -> None:
        d = self._config.device(vid)
        if d is None:
            return
        payload = {
            "version": SRC_VERSION,
            "sent_ts": int(time.time()),
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
            "sent_ts": int(time.time()),
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
            "sent_ts": int(time.time()),
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
                self._presence(args, energy=45)
            case "still":
                self._presence(args, energy=3)
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
            case "model":
                self._model(args)
            case "med":
                self._med(args)
            case "weather":
                self._weather(args)
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
            case _:
                print(f"모르는 명령: {verb} (help)")

    # ------------------------------------------------------------ 가전 명령

    def _device_power(self, device_type: str, args: list[str]) -> None:
        vid = self._first_of_type(device_type)
        if vid is None:
            print(f"{device_type} 가 설정에 없음")
            return
        on = not args or args[0] != "off"
        self._set(vid, power="ON" if on else "OFF")
        print(f"  {vid} power={'ON' if on else 'OFF'}")

    def _light(self, args: list[str]) -> None:
        """light [area] <0-100|on|off> — area 를 주면 그 구역 조명."""
        area = None
        if args and args[0] in self._config.areas:
            area, args = args[0], args[1:]

        vid = None
        for d in self._config.all_devices():
            if d.device_type == "smart_light" and d.enabled:
                if area is None or d.area == area:
                    vid = d.id
                    break
        if vid is None:
            print(f"smart_light 가 설정에 없음{f' ({area})' if area else ''}")
            return

        if not args or args[0] == "off":
            self._set(vid, power="OFF")
            print(f"  {vid} power=OFF")
            return
        if args[0] == "on":
            self._set(vid, power="ON", brightness=100)
            print(f"  {vid} power=ON brightness=100")
            return
        try:
            level = int(args[0])
        except ValueError:
            print("사용법: light [area] <0-100|on|off>")
            return
        self._set(vid, power="ON" if level else "OFF", brightness=level)
        print(f"  {vid} brightness={level}")

    def _fridge(self, args: list[str]) -> None:
        vid = self._first_of_type("smart_fridge")
        if vid is None:
            return
        opened = bool(args) and args[0] == "open"
        self._set(vid, door="OPEN" if opened else "CLOSED")
        if opened:
            self._publish_event(vid, "door_opened")
        else:
            self._publish_event(vid, "door_closed", open_duration_sec=12.0)
        print(f"  {vid} door={'OPEN' if opened else 'CLOSED'}")

    def _washer(self, args: list[str]) -> None:
        vid = self._first_of_type("washer")
        if vid is None:
            return
        cycle = args[0].upper() if args else "WASH"
        self._set(vid, power="ON", cycle=cycle, remain_min=30)
        print(f"  {vid} cycle={cycle}")

    def _clean(self, args: list[str]) -> None:
        vid = self._first_of_type("robot_cleaner")
        if vid is None:
            print("robot_cleaner 가 설정에 없음")
            return
        what = args[0] if args else "start"
        status = {"start": "CLEANING", "stop": "PAUSED", "dock": "DOCKED"}.get(what)
        if status is None:
            print("사용법: clean <start|stop|dock>")
            return
        self._set(vid, status=status)
        print(f"  {vid} status={status}")

    # ------------------------------------------------------------ 센서 명령

    def _presence_at(
        self,
        area: str,
        *,
        energy: int,
        present: bool = True,
        distance_cm: int = 150,
        exclusive: bool = True,
    ) -> str | None:
        """그 구역에 사람이 있다고 보낸다. 나머지 구역은 비운다.

        사람은 한 명이고 한 곳에만 있다. 다른 구역을 비우지 않으면 엔진이
        두 명으로 센다 — 실제 mmWave 도 사람이 나가면 스스로 present=false
        를 올리므로, 비우는 쪽이 하드웨어와 같다.

        exclusive=False 면 그 구역만 건드린다 (2인 상황 테스트).
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
                    self._publish_sensor(
                        ovid, present=False, confidence="high",
                        energy=0, distance_cm=0,
                    )

        self._publish_sensor(
            vid,
            present=present,
            confidence="high",
            energy=energy if present else 0,
            distance_cm=distance_cm if present else 0,
        )
        return vid

    def _presence(self, args: list[str], *, energy: int) -> None:
        """presence <area> [off|keep] — keep 면 다른 구역을 비우지 않는다 (2인 테스트)."""
        if not args:
            print("사용법: presence <area> [off|keep]")
            return
        area = args[0]
        flags = [a.lower() for a in args[1:]]
        present = "off" not in flags
        keep = "keep" in flags

        vid = self._presence_at(
            area, energy=energy, present=present, exclusive=not keep
        )
        if vid is None:
            return
        note = "  (다른 구역 유지)" if keep else ""
        print(f"  {vid} present={present} energy={energy if present else 0}{note}")

    def _motion(self, args: list[str]) -> None:
        """motion <area> [off] — PIR. presence 와 다른 경로를 탄다."""
        if not args:
            print("사용법: motion <area> [off]")
            return
        area = args[0]
        vid = self._sensor_in(area, "motion")
        if vid is None:
            print(f"{area} 에 motion 센서가 없음")
            return
        on = len(args) < 2 or args[1] != "off"
        self._publish_sensor(vid, motion=on, confidence="low")
        print(f"  {vid} motion={on}")

    def _bed(self, args: list[str]) -> None:
        """bed [area] [off] — area 를 주면 그 구역 압력 패드."""
        area = None
        if args and args[0] in self._config.areas:
            area, args = args[0], args[1:]

        vid = self._sensor_in(area, "bed") if area else self._sensor_of_type("bed")
        if vid is None:
            print(f"bed 센서가 없음{f' ({area})' if area else ''}")
            return
        occupied = not args or args[0] != "off"
        self._publish_sensor(vid, occupied=occupied)
        print(f"  {vid} occupied={occupied}")

    def _door(self, args: list[str]) -> None:
        vid = self._sensor_of_type("door")
        if vid is None:
            print("door 센서가 없음")
            return
        opened = bool(args) and args[0] == "open"
        self._publish_sensor(vid, open=opened)
        print(f"  {vid} open={opened}")

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

    def _lux(self, args: list[str]) -> None:
        vid = self._sensor_of_type("light")
        if vid is None:
            print("light 센서가 없음")
            return
        lux = float(args[0]) if args else 100.0
        self._publish_sensor(vid, illuminance_lux=lux)
        print(f"  {vid} {lux} lux")

    # ------------------------------------------------------------ 모델·복약

    def _model(self, args: list[str]) -> None:
        """model [late|normal] — KDE 모델을 쏜다.

        peaks 가 없으면 MEAL_PROMPT 도 식후 복약도 통째로 안 돈다.
        배치(RPi4)가 보내는 것이지만 테스트에서는 여기서 만든다.

            model          지금이 봉우리 한가운데 → TIME_NORMAL
            model late     봉우리를 지남 → ANOMALY (식사 알림)
        """
        late = bool(args) and args[0] == "late"
        now = datetime.now()
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
            "sent_ts": int(time.time()),
            "src_id": "rpi4",
            "trained_at": int(time.time()) - 3600,
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

        감기약 = 식후 1분 (식사 묶음이 닫히고 1분 뒤)
        혈압약 = 지금부터 2분 뒤 고정 시각
        """
        if args and args[0] == "clear":
            payload = {
                "version": SRC_VERSION, "sent_ts": int(time.time()),
                "src_id": "rpi5-api", "medications": [],
            }
            self._publish_raw("hestia/registry/medications", payload, retain=True)
            print("  복약 등록 비움")
            return

        today = date.today()
        end = today + timedelta(days=4)
        soon = datetime.now() + timedelta(minutes=2)

        payload = {
            "version": SRC_VERSION,
            "sent_ts": int(time.time()),
            "src_id": "rpi5-api",
            "medications": [
                {
                    "id": "med-test-cold",
                    "name": "감기약",
                    "schedule": {"type": "after_meal", "delay_min": 1},
                    "start_date": today.isoformat(),
                    "days": 5,
                    "end_date": end.isoformat(),
                    "refill_notice": True,          # end_date 가 4일 뒤 → 재처방도 걸린다
                },
                {
                    "id": "med-test-bp",
                    "name": "혈압약",
                    "schedule": {
                        "type": "fixed",
                        "times": [f"{soon.hour:02d}:{soon.minute:02d}"],
                    },
                    "start_date": today.isoformat(),
                    "days": 30,
                    "end_date": (today + timedelta(days=29)).isoformat(),
                    "refill_notice": False,
                },
            ],
        }
        self._publish_raw("hestia/registry/medications", payload, retain=True)
        print(
            f"  복약 등록 — 감기약(식후 1분), "
            f"혈압약({soon.hour:02d}:{soon.minute:02d} 고정)"
        )

    def _weather(self, args: list[str]) -> None:
        """weather [on|off|dry] — 특보를 쏜다.

        on   폭염 특보 → HEAT_ADVISORY 로 수분 알림
        dry  건조 특보
        off  특보 해제 (관측값만)

        observed_at 을 지금으로 넣는 것이 중요하다 — 엔진이
        age_sec <= weather.max_age_sec 를 보고 오래된 값을 버린다.
        """
        what = args[0].lower() if args else "on"
        now = int(time.time())

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
            "sent_ts": int(time.time()),
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
            "version": SRC_VERSION, "sent_ts": int(time.time()),
            "src_id": "rpi4", "profile": name,
        }
        self._publish_raw("hestia/system/profile", payload, retain=True)
        print(f"  프로파일 {name}")

    def _clear_retained(self) -> None:
        """retained 를 전부 지운다. 스키마를 바꿨을 때 옛 값이 남는다."""
        topics = ["hestia/model/kde", "hestia/registry/medications",
                  "hestia/external/weather", "hestia/system/profile"]
        for s in self._config.all_sensors():
            topics.append(f"hestia/sensor/{s.id}/state")
        for d in self._config.all_devices():
            topics.append(f"hestia/device/{d.id}/state")
        for t in topics:
            self._client.publish(t, b"", qos=1, retain=True)
        print(f"  retained {len(topics)}건 삭제")

    # ------------------------------------------------------------ 묶음 명령

    def _home(self) -> None:
        """집에 있는 상태로 되돌린다 — 거실 재실, 나머지 비움."""
        if self._presence_at("living", energy=45) is None:
            return
        print("  거실 재실, 나머지 구역 비움")

    def _out(self) -> None:
        """전 구역을 비운다. 현관문까지 여닫아 door_exit 근거를 만든다."""
        for area in self._config.areas:
            vid = self._sensor_in(area, "presence")
            if vid:
                self._publish_sensor(
                    vid, present=False, confidence="high", energy=0, distance_cm=0
                )
        for vid in self._sensors_of_type("bed"):
            self._publish_sensor(vid, occupied=False)
        door = self._sensor_of_type("door")
        if door:
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
        """
        kitchen = self._sensor_in("kitchen", "presence")
        induction = next(
            (s.id for s in self._config.all_sensors()
             if s.type == "power" and "MEAL" in s.roles), None
        )
        if kitchen is None or induction is None:
            print("주방 presence 또는 인덕션 전력 센서가 없음")
            return

        # 주방에 들어가면 나머지 구역은 비워진다 (_presence_at 이 한다).
        # 그러지 않으면 거실이 true 로 남아 엔진이 두 명으로 센다.
        def pres(energy: int) -> Callable[[], None]:
            return lambda: self._presence_at("kitchen", energy=energy)

        self._after(0, "주방 입장", pres(45))
        self._after(2, "인덕션 ON → COOKING", lambda: self._publish_sensor(
            induction, watt=1200.0, state="ON"))
        self._after(20, "인덕션 OFF", lambda: self._publish_sensor(
            induction, watt=2.0, state="OFF"))
        self._after(23, "냉장고 열고 닫기", self._fridge_cycle)
        self._after(26, "주방 체류 저에너지 → EATING", pres(5))
        self._after(50, "주방 이탈", lambda: self._publish_sensor(
            kitchen, present=False, confidence="high", energy=0, distance_cm=0))
        self._after(52, "거실로", lambda: self._presence_at("living", energy=45))
        self._after(
            80,
            "묶음 종료 대기 — grace 20초 뒤 log/t0, 다시 1분 뒤 복약 알림",
            lambda: None,
        )

    def _fridge_cycle(self) -> None:
        vid = self._first_of_type("smart_fridge")
        if vid is None:
            return
        self._set(vid, door="OPEN")
        self._publish_event(vid, "door_opened")
        self._after(2, "", lambda: (
            self._set(vid, door="CLOSED"),
            self._publish_event(vid, "door_closed", open_duration_sec=2.0),
        ))

    def _sc_sleep(self) -> None:
        """침실 재실 → 정지 → 프로브 → 확정.

        config-test 기준: probe_still 30초, no_channel 60초, timeout 10초.
        침실 조명을 켜두면 dim 프로브, 꺼두면 긴 정지만으로 확정한다.
        """
        if self._sensor_in("bedroom", "presence") is None:
            print("침실 presence 센서가 없음")
            return
        pad = self._sensor_in("bedroom", "bed")

        # 입장이 나머지 구역을 비운다 — 따로 '거실 비움' 단계가 필요 없다
        self._after(0, "침실 입장", lambda: self._presence_at(
            "bedroom", energy=45, distance_cm=120))
        if pad:
            self._after(3, "침대 점유", lambda: self._publish_sensor(pad, occupied=True))
        self._after(5, "정지 시작 (energy 바닥)", lambda: self._presence_at(
            "bedroom", energy=2, distance_cm=120))
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
        """
        if self._sensor_in("bathroom", "presence") is None:
            print("화장실 presence 센서가 없음")
            return
        self._after(0, "화장실 입장", lambda: self._presence_at(
            "bathroom", energy=45, distance_cm=100))
        self._after(3, "정지 — energy 바닥", lambda: self._presence_at(
            "bathroom", energy=2, distance_cm=100))
        self._after(40, "30초 정지 — SAFETY 알림이 나와야 한다", lambda: None)

    # ------------------------------------------------------------ 출력

    def _show(self) -> None:
        for vid in sorted(self.devices):
            d = self._config.device(vid)
            fields = " ".join(f"{k}={v}" for k, v in self.devices[vid].items())
            print(f"  {vid:8} {d.device_type:16} {fields}")
        if self._pending:
            print(f"  전환 대기 {len(self._pending)}건")
        if self._script:
            print(f"  예약 단계 {len(self._script)}건")
        if self._last_notify_id:
            print(f"  마지막 알림 {self._last_notify_id}")


SCENARIOS = """
scenario <이름> — 단계를 시간 간격으로 자동 실행 (abort 로 취소)

  meal       조리 → 식사 → 이탈 → 묶음 종료 → 식후 복약
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
  light [area] <0-100|on|off>
  fridge [open|close]      냉장고 문
  washer [WASH|RINSE|SPIN|DONE]
  clean <start|stop|dock>  로봇 청소기
  dispense                 정수기 급수 이벤트
  remote                   TV 리모컨 입력 이벤트

센서
  presence <area> [off|keep]  재실 (energy 45). 나머지 구역은 비워진다
                                keep 를 주면 안 비운다 — 2인 상황 테스트용
  still <area> [keep]      정지 (energy 3) — 낙상·수면 판정용
  motion <area> [off]      PIR
  bed [area] [off]         압력 패드
  door [open|close]        현관문
  power <vid> <watt>       전력 센서
  climate <온도> <습도>     온습도 — HEAT_ONSET / DRY_ONSET
  lux <값>                 조도

배치·앱이 보내는 것
  model [late]             KDE 모델. 없으면 식사·복약이 안 돈다
                             (기본) 지금이 봉우리 중심 → TIME_NORMAL
                             late   봉우리를 지남 → 식사 알림
  med [clear]              복약 등록 (감기약 식후 1분 / 혈압약 2분 뒤)
  weather [on|off|dry]     특보. on=폭염 → HEAT_ADVISORY 수분 알림
  profile [REAL|DEMO]      프로파일 전환
  ack [SEEN|DELIVERED]     마지막 알림에 응답

묶음
  home                     거실 재실, 나머지 비움
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