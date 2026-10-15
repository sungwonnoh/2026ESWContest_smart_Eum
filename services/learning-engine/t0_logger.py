"""hestia/log/t0 수신기 (RPi4).

Context Engine 이 활동 묶음이 끝날 때마다 발행하는 t0 를 JSONL 로 쌓는다.
baseline.py 는 이 파일을 --t0-jsonl 로 읽는다.

    RPi5 FileT0Log → hestia/log/t0 → 이 수신기 → t0_log.jsonl → run_learning.py

- 학습이 읽는 검증기(parse_t0_record)를 그대로 통과한 것만 쌓는다. 깨진 줄이 학습을 막지 않게 하는 쪽이 수신 쪽 책임이다.
- QoS 1 은 같은 메시지가 두 번 올 수 있다. (type, t0) 로 중복을 거른다 — 같은 식사가 두 번 학습되면 분포가 틀어진다.
- 세션을 유지한다(clean_session=False). 이 수신기가 꺼져 있는 동안 온 메시지를 브로커가 모아 두었다가 돌려준다.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
from pathlib import Path
from typing import Any, Callable

from baseline import parse_t0_record

log = logging.getLogger(__name__)

TOPIC = "hestia/log/t0"
QOS = 1
CLIENT_ID = "hestia-t0-logger"      # 고정. 바뀌면 브로커가 세션을 못 이어 간다

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DATA_DIR = Path(os.getenv("HESTIA_DATA", "/data/hestia"))
DEFAULT_PATH = DATA_DIR / "t0_log.jsonl"


def _key(obj: dict[str, Any]) -> tuple[str, float]:
    return str(obj["type"]), float(obj["t0"])


class T0Store:
    """JSONL 에 t0 를 쌓는다. 이미 있는 (type, t0) 는 다시 쓰지 않는다."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._seen: set[tuple[str, float]] = set()
        self._load()

    def _load(self) -> None:
        """재시작해도 중복을 걸러내도록 기존 파일에서 키를 복원한다."""
        if not self._path.exists():
            return
        with self._path.open(encoding="utf-8") as fp:
            for line in fp:
                try:
                    self._seen.add(_key(json.loads(line)))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue

    def __len__(self) -> int:
        return len(self._seen)

    def add(self, raw: bytes | str) -> bool:
        """쌓았으면 True. 깨졌거나 이미 있으면 False."""
        try:
            obj = json.loads(raw)
            parse_t0_record(obj)            # 학습이 읽는 검증기와 같은 것
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            log.warning("t0 버림 — %s", exc)
            return False

        key = _key(obj)
        if key in self._seen:
            log.debug("t0 중복 — %s", key)
            return False

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._seen.add(key)
        log.info("t0 기록 %s t0=%s", obj["type"], obj["t0"])
        return True


def default_client_factory():
    import paho.mqtt.client as mqtt

    return mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=CLIENT_ID,
        clean_session=False,
    )


def run(
    store: T0Store,
    host: str = MQTT_HOST,
    port: int = MQTT_PORT,
    client_factory: Callable = default_client_factory,
):
    """연결해 구독하고 돌아온다. 호출부가 loop 를 돌린다 (테스트에서 가짜 client 를 쓰려고)."""
    client = client_factory()

    def on_connect(client, userdata, flags, reason_code, properties=None):
        # 재연결 때마다 다시 건다 — 세션이 이어져도 안전하다
        client.subscribe(TOPIC, qos=QOS)
        log.info("구독 %s (RPi5 %s:%s)", TOPIC, host, port)

    def on_message(client, userdata, msg):
        store.add(msg.payload)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(host, port, 60)    # 브로커가 늦게 떠도 재시도한다
    return client


def main() -> None:
    parser = argparse.ArgumentParser(description="hestia/log/t0 → JSONL 수신기")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--host", default=MQTT_HOST)
    parser.add_argument("--port", type=int, default=MQTT_PORT)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    store = T0Store(args.path)
    log.info("t0 수신기 시작 — %s (기존 %d건)", args.path, len(store))

    client = run(store, args.host, args.port)

    def stop(*_):
        client.disconnect()
        client.loop_stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
