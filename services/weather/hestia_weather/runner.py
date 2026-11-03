"""실시간 실행기 (RPi4).

    hestia/external/weather       발행. 새 실황·특보 변경(update), 요청 응답(reply). QoS 1, retained 아님
    hestia/external/weather/get   구독. Context Engine 이 재시작 직후나 필요할 때 보낸다
    hestia/external/weather/demo  구독. 시연 모드 {"heat": true|false, "ttl_sec"?}. Backend 가 앱 요청으로 보낸다

실행:
    KMA_SERVICE_KEY=... MQTT_HOST=<rpi5> python -m hestia_weather.runner
    systemd 는 /etc/hestia/weather.env 를 EnvironmentFile 로 읽는다 (deploy/hestia-weather.service)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import signal
import threading
import time
from pathlib import Path
from typing import Callable

import paho.mqtt.client as mqtt

from .config import ConfigError, load_location
from .kma import KmaClient
from .service import DEMO_TTL_SEC, WeatherService

log = logging.getLogger(__name__)

WEATHER_TOPIC = "hestia/external/weather"
GET_TOPIC = "hestia/external/weather/get"
DEMO_TOPIC = "hestia/external/weather/demo"

TICK_INTERVAL_SEC = 0.5
RECONNECT_MIN_DELAY_SEC = 1
RECONNECT_MAX_DELAY_SEC = 30


class Runner:
    """MQTT 네트워크는 paho 스레드(loop_start)가, 날씨 조회와 발행 판단은 run() 이 맡는다.

    콜백은 신호만 남기고 WeatherService 는 run() 쪽에서만 만진다. 기상청 조회가
    느려도 keepalive 가 밀리지 않고, 브로커가 죽어도 조회가 멈추지 않는다.
    """

    def __init__(
        self,
        service: WeatherService,
        *,
        host: str = "localhost",
        port: int = 1883,
        clock: Callable[[], float] = time.time,
        client: mqtt.Client | None = None,
    ) -> None:
        self.service = service
        self._host = host
        self._port = port
        self._clock = clock
        self._running = False
        self._requested = threading.Event()     # get 을 받음. 응답은 루프에서
        self._resend = threading.Event()        # (재)연결됨. 캐시 재발행은 루프에서
        self._demo: queue.SimpleQueue[dict] = queue.SimpleQueue()   # 받은 시연 명령
        self._connect_fail_logged = False       # 재시도마다 찍지 않게 끊긴 동안 한 번만
        self.client = client or self._make_client()

    # ------------------------------------------------------------ 생애

    def start(self) -> None:
        """브로커(RPi5)가 늦게 떠도, 나중에 끊겨도 paho 스레드가 다시 붙는다."""
        self._running = True
        log.info("브로커 연결 시작: %s:%s", self._host, self._port)
        self.client.connect_async(self._host, self._port, keepalive=60)
        self.client.loop_start()

    def run(self) -> None:
        self.start()
        try:
            while self._running:
                try:
                    self.tick()
                except Exception:                     # noqa: BLE001
                    # 한 번의 예외로 서비스가 멈추면 안 된다
                    log.exception("루프 예외 — 계속한다")
                time.sleep(TICK_INTERVAL_SEC)
        finally:
            self._shutdown()

    def stop(self) -> None:
        """시그널 핸들러에서도 부른다. 표시만 하고 정리는 run() 이 끝나면서 한다."""
        self._running = False

    def _shutdown(self) -> None:
        log.info("종료")
        try:
            self.client.disconnect()
            self.client.loop_stop()
        except Exception:                             # noqa: BLE001
            log.warning("종료 중 예외", exc_info=True)

    def tick(self) -> None:
        """조회할 때가 됐으면 조회하고(새 내용이면 발행), 요청이 있었으면 응답한다."""
        now = self._clock()
        resend = _take(self._resend)
        changed = False

        while not self._demo.empty():
            cmd = self._demo.get_nowait()
            changed |= self.service.set_demo(bool(cmd.get("heat")), now, float(cmd.get("ttl_sec") or DEMO_TTL_SEC))

        changed |= self.service.demo_expired(now)

        if self.service.due(now):
            changed |= self.service.poll(now)

        if changed or (resend and self.service.cache is not None):
            self._publish(reason="update")

        if _take(self._requested):
            if self.service.reply(now):
                self._publish(reason="reply")
            else:
                log.warning("요청을 받았지만 아직 날씨가 없음 — 응답하지 않음")

    # ------------------------------------------------------------ MQTT

    def _make_client(self) -> mqtt.Client:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"hestia-weather-{os.getpid()}",
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_connect_fail = self._on_connect_fail
        client.reconnect_delay_set(min_delay=RECONNECT_MIN_DELAY_SEC, max_delay=RECONNECT_MAX_DELAY_SEC)
        # 콜백 예외가 네트워크 스레드를 죽이면 재연결도 함께 멈춘다. 로그만 남기고 계속한다
        client.suppress_exceptions = True
        client.enable_logger(log)
        return client

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        """(재)연결될 때마다 구독. 끊긴 동안 보내지 못한 실황이 있을 수 있어 캐시를 한 번 보낸다."""
        if reason_code != 0:
            log.warning("연결 거부 rc=%s", reason_code)
            return
        client.subscribe(GET_TOPIC, qos=1)
        client.subscribe(DEMO_TOPIC, qos=1)
        log.info("연결됨 — 구독 %s, %s", GET_TOPIC, DEMO_TOPIC)
        self._connect_fail_logged = False
        self._resend.set()

    def _on_connect_fail(self, client, userdata) -> None:
        if not self._connect_fail_logged:
            self._connect_fail_logged = True
            log.warning("브로커 연결 실패 %s:%s — 계속 재시도한다", self._host, self._port)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if self._running:
            log.warning("연결 끊김 rc=%s — 재연결 대기", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        # 누군가 get 을 retained 로 남겨도 매 연결마다 응답하지 않게 무시한다
        if msg.retain:
            log.warning("retained 요청 무시: %s", msg.topic)
            return
        if msg.topic == GET_TOPIC:
            self._requested.set()               # 응답은 루프(tick)에서 — 콜백에서 HTTP 를 부르지 않는다
        elif msg.topic == DEMO_TOPIC:
            try:
                cmd = json.loads(msg.payload)
            except ValueError:
                log.warning("시연 명령 형식 오류: %r", msg.payload)
                return
            if isinstance(cmd, dict):
                self._demo.put(cmd)

    def _publish(self, *, reason: str) -> None:
        # paho 는 끊긴 동안의 QoS 1 을 쌓아 뒀다 한꺼번에 보낸다. 지난 날씨는 쌓지 않고
        # 다시 연결될 때 _resend 로 최신 값만 보낸다.
        if not self.client.is_connected():
            log.info("브로커 연결 없음 — 발행 건너뜀 (%s)", reason)
            return
        payload = self.service.payload(reason=reason, now=self._clock())
        info = self.client.publish(WEATHER_TOPIC, json.dumps(payload, ensure_ascii=False), qos=1, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("발행 실패 rc=%s (%s)", info.rc, reason)


def _take(flag: threading.Event) -> bool:
    """신호를 읽고 지운다. 지운 뒤에 온 신호는 다음 tick 이 본다."""
    if not flag.is_set():
        return False
    flag.clear()
    return True


# ==================================================================== 조립


def build(args: argparse.Namespace) -> Runner:
    key = os.environ.get("KMA_SERVICE_KEY", "")
    if not key:
        raise ConfigError("환경변수 KMA_SERVICE_KEY 가 없음 (/etc/hestia/weather.env)")

    home_path = Path(args.config) / "homes" / f"{args.home}.toml"
    location = load_location(home_path)
    log.info(
        "위치: %s → 격자 (%d, %d), 특보 구역 %s",
        location.name or home_path.name, location.nx, location.ny,
        list(location.warning_areas) or "없음 (특보 조회 안 함)",
    )

    client = KmaClient(key, location.nx, location.ny)
    service = WeatherService(client, location, src_id=args.src_id)
    return Runner(service, host=args.host, port=args.port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HESTIA 외부 날씨 (RPi4)")
    parser.add_argument(
        "--config",
        default=os.environ.get("HESTIA_CONFIG", "/data/hestia/config"),
        help="homes/ 가 있는 디렉터리",
    )
    parser.add_argument("--home", default=os.environ.get("HESTIA_HOME", "demo"))
    parser.add_argument("--host", default=os.environ.get("MQTT_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    parser.add_argument("--src-id", default=os.environ.get("HESTIA_SRC_ID", "rpi4"))
    parser.add_argument("--once", action="store_true", help="한 번 조회해 출력만 하고 끝낸다 (MQTT 없음)")
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

    if args.once:
        now = time.time()
        runner.service.poll(now)
        if runner.service.cache is None:
            log.error("실황 조회 실패")
            return 1
        print(json.dumps(runner.service.payload(reason="update", now=now), ensure_ascii=False, indent=2))
        return 0

    # systemd 가 SIGTERM 을 보낸다
    signal.signal(signal.SIGTERM, lambda *_: runner.stop())
    signal.signal(signal.SIGINT, lambda *_: runner.stop())

    runner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
