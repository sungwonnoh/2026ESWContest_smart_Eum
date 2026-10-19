"""실시간 실행기.

구독
    tele/hestia-<vid>/SENSOR    Tasmota 가 TelePeriod / PowerDelta 로 보낸다
    tele/hestia-<vid>/LWT       브로커가 쓴다 (retained). 센서와 가전 둘 다
    stat/hestia-<vid>/RESULT    전구의 명령 응답
    tele/hestia-<vid>/STATE     전구의 주기 보고
    hestia/device/<vid>/cmd     엔진의 제어
    hestia/sim/clock            시연용 가상 시계

발행
    hestia/sensor/<vid>/state   QoS 1, retained (power 는 순간값이 아니다)
    hestia/device/<vid>/state   QoS 1, retained
    cmnd/hestia-<vid>/Backlog   전구에 내리는 명령

실행:
    MQTT_HOST=<rpi5> python -m hestia_tasmota.runner

Tasmota 콘솔에서 한 번 해둘 설정:
    Topic hestia-vs-07     토픽을 vid 로 맞춘다 — 매핑 표가 필요 없어진다
    TelePeriod 10          기본 300초로는 전자레인지 3분 사용을 통째로 놓친다
    PowerDelta 20          전력이 20% 변하면 즉시 — 켜지는 순간을 잡는다
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Callable

import paho.mqtt.client as mqtt

from .config import ConfigError, load_devices, load_sensors
from .light import build_backlog, kelvin_to_mired, parse_result, speed_for
from .service import LWT_SUFFIX, BridgeService
from .tasmota import TasmotaError, parse_lwt, parse_power

log = logging.getLogger(__name__)

SIM_CLOCK_TOPIC = "hestia/sim/clock"
LOOP_TIMEOUT_SEC = 0.5


class Runner:
    def __init__(
        self,
        service: BridgeService,
        *,
        host: str = "localhost",
        port: int = 1883,
        clock: Callable[[], float] = time.time,
        client: mqtt.Client | None = None,
        speed_step_sec: float = 0.5,
        max_speed: int = 40,
    ) -> None:
        self.service = service
        self._host = host
        self._port = port
        self._clock = clock
        self._running = False
        # Speed 한 칸이 몇 초인지는 Tasmota 문서에서 확인하지 못했다.
        # 플러그가 오면 콘솔에서 재어 맞춘다.
        self._speed_step = speed_step_sec
        self._max_speed = max_speed
        self.client = client or self._make_client()

    # ============================================================ 생애

    def start(self) -> None:
        """브로커가 늦게 떠도 루프에서 재연결한다."""
        self._running = True
        try:
            self.client.connect(self._host, self._port, keepalive=60)
            log.info("브로커 연결: %s:%s", self._host, self._port)
        except OSError as exc:
            log.warning("브로커 연결 실패 (%s) — 루프에서 재시도한다", exc)
            self.client.connect_async(self._host, self._port, keepalive=60)

    def run(self) -> None:
        self.start()
        while self._running:
            try:
                self.client.loop(timeout=LOOP_TIMEOUT_SEC)
            except Exception:                         # noqa: BLE001
                # 한 번의 예외로 브리지가 멈추면 안 된다
                log.exception("루프 예외 — 계속한다")
                time.sleep(LOOP_TIMEOUT_SEC)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        log.info("종료")
        try:
            self.client.disconnect()
        except Exception:                             # noqa: BLE001
            log.warning("종료 중 예외", exc_info=True)

    # ============================================================ MQTT

    def _make_client(self) -> mqtt.Client:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"hestia-tasmota-{os.getpid()}",
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        return client

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            log.warning("연결 거부 rc=%s", reason_code)
            return
        for topic in self.service.subscriptions():
            client.subscribe(topic, qos=1)
        for topic in self.service.device_subscriptions():
            client.subscribe(topic, qos=1)
        client.subscribe(SIM_CLOCK_TOPIC, qos=1)
        log.info(
            "연결됨 — 센서 %d대, 가전 %d대",
            len(self.service.vids), len(self.service.device_vids),
        )

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if self._running:
            log.warning("연결 끊김 rc=%s — 재연결 대기", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        """가전 토픽을 먼저 가린다 — 센서 분기가 모르는 토픽을 바로
        버리기 때문에, 순서가 바뀌면 조명이 영영 닿지 못한다."""
        if msg.topic == SIM_CLOCK_TOPIC:
            self._on_clock(msg.payload)
            return

        vid = self.service.cmd_vid(msg.topic)
        if vid is not None:
            self._on_cmd(vid, msg.payload)
            return

        vid = self.service.result_vid(msg.topic)
        if vid is not None:
            self._on_result(vid, msg.payload)
            return

        found = self.service.vid_for(msg.topic)
        if found is None:
            log.debug("모르는 토픽 무시: %s", msg.topic)
            return
        vid, suffix = found
        if suffix == LWT_SUFFIX:
            self._on_lwt(vid, msg.payload)
        else:
            self._on_sensor(vid, msg.payload)

    # ============================================================ 처리

    def _on_clock(self, raw: bytes) -> None:
        try:
            offset = float(json.loads(raw).get("offset", 0.0))
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            log.warning("시계 페이로드 해석 실패")
            return
        if self.service.set_clock_offset(offset):
            log.info("가상 시계 offset=%+.0fs", offset)

    # ------------------------------------------------------------ 센서

    def _on_sensor(self, vid: str, raw: bytes) -> None:
        try:
            watt = parse_power(raw)
        except TasmotaError as exc:
            log.warning("%s SENSOR 해석 실패 — %s", vid, exc)
            return
        if watt is None:
            log.debug("%s SENSOR 에 ENERGY 없음 — 무시", vid)
            return
        self._publish_sensor(vid, watt)

    def _publish_sensor(self, vid: str, watt: float) -> None:
        payload = self.service.power_payload(vid, watt, self._clock())
        info = self.client.publish(
            self.service.state_topic(vid),
            json.dumps(payload, ensure_ascii=False),
            qos=1,
            retain=True,                    # power 는 순간값이 아니다 (명세)
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("발행 실패 rc=%s (%s)", info.rc, vid)
            return
        log.info("%s %.1fW", vid, watt)

    # ------------------------------------------------------------ 가전

    def _on_cmd(self, vid: str, raw: bytes) -> None:
        try:
            doc = json.loads(raw)
            action = doc.get("action", "set")
            params = dict(doc.get("params") or {})
        except (json.JSONDecodeError, AttributeError, TypeError):
            log.warning("%s cmd 해석 실패", vid)
            return

        if action != "set":
            log.warning("%s 가 받을 수 없는 action: %s", vid, action)
            return

        ms = params.pop("transition_ms", None)
        speed = speed_for(ms, step_sec=self._speed_step, max_speed=self._max_speed)
        if ms and speed == self._max_speed:
            # 조용히 짧아지면 수면 프로브가 왜 빨리 끝나는지 알 수 없다
            log.warning(
                "%s 전환 %dms 가 Speed 상한(%d)에 걸려 짧아진다",
                vid, int(ms), self._max_speed,
            )

        try:
            line = build_backlog(params, speed=speed)
        except ValueError as exc:
            log.warning("%s cmd 를 옮길 수 없음 — %s", vid, exc)
            return
        if line is None:
            log.warning("%s cmd 에 옮길 값이 없음: %s", vid, params)
            return

        info = self.client.publish(self.service.cmnd_topic(vid), line, qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("cmnd 발행 실패 rc=%s (%s)", info.rc, vid)
            return

        ct = params.get("color_temp")
        self.service.note_sent(
            vid, params, kelvin_to_mired(int(ct)) if ct is not None else None
        )
        log.info("%s → %s", vid, line)

    def _on_result(self, vid: str, raw: bytes) -> None:
        try:
            fields = parse_result(raw)
        except TasmotaError as exc:
            log.warning("%s 응답 해석 실패 — %s", vid, exc)
            return
        if not fields:
            return                          # 조명과 무관한 STATE (WiFi 등)

        state = self.service.apply_result(vid, fields)
        if state is None:
            log.debug("%s 전원을 아직 모름 — State 를 물어본다", vid)
            self.client.publish(self.service.query_topic(vid), "", qos=1)
            return

        payload = self.service.device_payload(vid, state, self._clock())
        info = self.client.publish(
            self.service.device_state_topic(vid),
            json.dumps(payload, ensure_ascii=False),
            qos=1,
            retain=True,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("state 발행 실패 rc=%s (%s)", info.rc, vid)
            return
        log.info("%s %s", vid, " ".join(f"{k}={v}" for k, v in state.items()))

    # ------------------------------------------------------------ 생존

    def _on_lwt(self, vid: str, raw: bytes) -> None:
        try:
            online = parse_lwt(raw)
        except TasmotaError as exc:
            log.warning("%s LWT 해석 실패 — %s", vid, exc)
            return
        if not self.service.mark_online(vid, online):
            return                          # 첫 수신이거나 같은 값

        if online:
            log.info("%s 온라인", vid)
            if not self.service.is_sensor(vid):
                # 전구가 지금 어떤 상태인지 모른다 — 물어본다
                self.client.publish(self.service.query_topic(vid), "", qos=1)
            return

        if self.service.is_sensor(vid):
            # 끊긴 플러그의 마지막 와트가 retained 로 남으면 엔진은 가전이
            # 계속 켜져 있다고 믿는다. 0 을 쏘는 쪽이 안전하다 —
            # '꺼진 것으로 보임' 이 '영원히 켜진 것으로 보임' 보다 낫다.
            log.warning("%s 오프라인 — watt=0 으로 덮는다", vid)
            self._publish_sensor(vid, 0.0)
            return

        # 조명은 덮지 않는다. power=OFF 를 쏘면 reverted() 가 참이 되어
        # manual_override 가 걸리고, 전구가 돌아와도 엔진이 손대지 않는다.
        log.warning("%s 오프라인 — 마지막 상태를 유지한다", vid)


# ==================================================================== 조립


def build(args: argparse.Namespace) -> Runner:
    home_path = Path(args.config) / "homes" / f"{args.home}.toml"
    sensors = load_sensors(home_path)
    devices = load_devices(home_path)
    if not sensors and not devices:
        raise ConfigError(
            f'{home_path}: source = "tasmota" 인 항목이 없음 — 브리지가 할 일이 없다'
        )

    log.info(
        "Tasmota 센서 %d대: %s", len(sensors),
        ", ".join(f"{s.vid}({s.sensor_type})" for s in sensors) or "없음",
    )
    log.info(
        "Tasmota 가전 %d대: %s", len(devices),
        ", ".join(f"{d.vid}({d.device_type})" for d in devices) or "없음",
    )

    service = BridgeService(sensors, devices, topic_prefix=args.topic_prefix)
    return Runner(
        service,
        host=args.host,
        port=args.port,
        speed_step_sec=args.speed_step,
        max_speed=args.max_speed,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HESTIA Tasmota 브리지")
    parser.add_argument(
        "--config",
        default=os.environ.get("HESTIA_CONFIG", "/data/hestia/config"),
        help="homes/ 가 있는 디렉터리",
    )
    parser.add_argument("--home", default=os.environ.get("HESTIA_HOME", "demo"))
    parser.add_argument("--host", default=os.environ.get("MQTT_HOST", "localhost"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883"))
    )
    parser.add_argument(
        "--topic-prefix",
        default=os.environ.get("TASMOTA_PREFIX", "hestia-"),
        help="Tasmota 토픽 접두어. 기기 토픽은 <접두어><vid> 여야 한다",
    )
    parser.add_argument(
        "--speed-step",
        type=float,
        default=float(os.environ.get("TASMOTA_SPEED_STEP", "0.5")),
        help="Tasmota Speed 한 칸의 초. 플러그에서 재어 맞춘다 (문서 미확인)",
    )
    parser.add_argument(
        "--max-speed",
        type=int,
        default=int(os.environ.get("TASMOTA_MAX_SPEED", "40")),
        help="Speed 상한. 넘는 전환은 잘리고 경고가 남는다",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        runner = build(args)
    except ConfigError as exc:
        log.error("설정 오류 — 기동하지 않는다: %s", exc)
        return 1

    # systemd 가 SIGTERM 을 보낸다
    signal.signal(signal.SIGTERM, lambda *_: runner.stop())
    signal.signal(signal.SIGINT, lambda *_: runner.stop())

    runner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())