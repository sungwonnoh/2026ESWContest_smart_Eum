"""ThinQ 브리지.

    replay  samples/*.json  →  변환  →  hestia/device/{vid}/state
    live    ThinQ Connect   →  변환  →  hestia/device/{vid}/state
            hestia/device/{vid}/cmd  →  변환  →  ThinQ 제어

두 입구가 **같은 변환기**를 쓴다. 실물 가전이 없어 live 는 검증되지
않았고, replay 와 변환기는 테스트로 검증했다. README 를 볼 것.

SDK 는 live 에서만 늦게 들여온다 — 안 깔아도 replay 가 돌아야 한다
(context-engine 의 Inventory.load_config 가 tomllib 을 그렇게 다룬다).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

from .device import TO_HESTIA, TO_THINQ, ThinqError

log = logging.getLogger("hestia_thinq")

SRC_VERSION = 1
SOURCE = "thinq"          # messages.py SOURCE 에 등록된 값


def state_topic(vid: str) -> str:
    return f"hestia/device/{vid}/state"


def cmd_topic(vid: str) -> str:
    return f"hestia/device/{vid}/cmd"


def envelope(vid: str, device_type: str, fields: dict[str, Any], seq: int) -> dict:
    """엔진이 받는 가전 상태 봉투. parse_device_state 가 읽는 모양이다."""
    return {
        "version": SRC_VERSION,
        "sent_ts": int(time.time()),
        "src_id": vid,
        "seq": seq,
        "device_type": device_type,
        "source": SOURCE,
        **fields,
    }


class Bridge:
    def __init__(self, host: str, port: int, vid: str, device_type: str) -> None:
        self.vid = vid
        self.device_type = device_type
        self._seq = 0
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"hestia-thinq-{os.getpid()}",
        )
        self._host, self._port = host, port

    def connect(self) -> None:
        self.client.connect(self._host, self._port, keepalive=60)

    def publish_status(self, status: dict[str, Any]) -> dict[str, Any]:
        """ThinQ 상태 하나를 옮겨 발행한다. 발행한 봉투를 돌려준다."""
        convert = TO_HESTIA.get(self.device_type)
        if convert is None:
            raise ThinqError(f"변환기가 없는 기기 종류: {self.device_type}")
        fields = convert(status)

        self._seq += 1
        body = envelope(self.vid, self.device_type, fields, self._seq)
        # retained 로 둔다. 가전 상태는 엔진이 늦게 떠도 알아야 하는 값이고
        # 시뮬레이터도 같은 선택을 한다
        info = self.client.publish(state_topic(self.vid), json.dumps(body), qos=1, retain=True)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("state 발행 실패 rc=%s", info.rc)
        return body


# ──────────────────────────────────────────────────────────── replay

def run_replay(args: argparse.Namespace) -> int:
    status = json.loads(Path(args.replay).read_text(encoding="utf-8"))
    # 문서 예시를 그대로 붙여넣은 파일이면 봉투가 남아 있을 수 있다.
    # SDK 는 벗겨서 주므로 여기서도 벗겨 같은 모양으로 맞춘다
    if "response" in status and isinstance(status["response"], dict):
        status = status["response"]

    bridge = Bridge(args.host, args.port, args.vid, args.device_type)
    bridge.connect()
    bridge.client.loop_start()
    try:
        body = bridge.publish_status(status)
    except ThinqError as exc:
        print(f"  옮기지 못함 — {exc}", file=sys.stderr)
        return 1
    finally:
        time.sleep(0.3)                  # 발행이 나갈 시간
        bridge.client.loop_stop()
        bridge.client.disconnect()

    print(f"  {state_topic(args.vid)}")
    print("  " + json.dumps(body, ensure_ascii=False))
    return 0


# ──────────────────────────────────────────────────────────── live

def run_live(args: argparse.Namespace) -> int:
    """실물 ThinQ 경로. 가전이 없어 검증하지 못했다 — README 를 볼 것."""
    import asyncio

    from aiohttp import ClientSession
    from thinqconnect.thinq_api import ThinQApi

    token = os.environ.get("THINQ_PAT")
    if not token:
        print("  THINQ_PAT 가 없다. connect-pat.lgthinq.com 에서 발급한다",
              file=sys.stderr)
        return 2

    # client_id 는 클라이언트마다 하나. 공식 SDK 가 uuid4 를 권하고,
    # 너무 많이 만들면 호출이 막힐 수 있다고 적어두었다. 그래서 환경변수로
    # 고정해 재사용한다
    client_id = os.environ.get("THINQ_CLIENT_ID") or str(uuid.uuid4())

    async def main() -> int:
        async with ClientSession() as session:
            api = ThinQApi(
                session=session,
                access_token=token,
                country_code=args.country,
                client_id=client_id,
            )

            devices = await api.async_get_device_list()
            # 가전이 없으면 빈 값이 온다. 그래도 인증·네트워크·응답 파싱이
            # 살아 있다는 증거다
            log.info("ThinQ 기기 %d대", len(devices or []))
            for d in devices or []:
                log.info("  %s  %s", d.get("deviceId"),
                         (d.get("deviceInfo") or {}).get("alias"))

            if not args.device_id:
                print("  --device-id 가 없다. 위 목록에서 고른다", file=sys.stderr)
                return 0

            bridge = Bridge(args.host, args.port, args.vid, args.device_type)
            bridge.connect()

            def on_cmd(_c, _u, msg) -> None:
                try:
                    doc = json.loads(msg.payload.decode("utf-8"))
                    params = dict(doc.get("params") or {})
                except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                    log.warning("cmd 해석 실패")
                    return
                params.pop("transition_ms", None)     # 공기청정기에 점진 변화가 없다
                convert = TO_THINQ.get(args.device_type)
                if convert is None:
                    return
                try:
                    body = convert(params)
                except ThinqError as exc:
                    log.warning("cmd 를 옮길 수 없음 — %s", exc)
                    return
                asyncio.run_coroutine_threadsafe(
                    api.async_post_device_control(args.device_id, body), loop
                )
                log.info("제어 %s", body)

            loop = asyncio.get_running_loop()
            bridge.client.on_message = on_cmd
            bridge.client.subscribe(cmd_topic(args.vid), qos=1)
            bridge.client.loop_start()

            try:
                while True:
                    status = await api.async_get_device_status(args.device_id)
                    try:
                        bridge.publish_status(status or {})
                    except ThinqError as exc:
                        log.warning("상태를 옮길 수 없음 — %s", exc)
                    # 폴링이다. 푸시(async_post_event_subscribe + MQTT)로
                    # 바꾸는 것이 다음 단계다 — README 를 볼 것
                    await asyncio.sleep(args.interval)
            finally:
                bridge.client.loop_stop()
                bridge.client.disconnect()

    return asyncio.run(main())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="HESTIA ThinQ 브리지")
    p.add_argument("--host", default=os.environ.get("MQTT_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    p.add_argument("--vid", default="vd-12", help="HESTIA virtual_id")
    p.add_argument("--device-type", default="air_purifier")
    p.add_argument("--replay", help="ThinQ 상태 JSON 파일 — 한 번 옮기고 끝낸다")
    p.add_argument("--live", action="store_true", help="실물 ThinQ 경로 (미검증)")
    p.add_argument("--device-id", help="live: ThinQ deviceId")
    p.add_argument("--country", default=os.environ.get("THINQ_COUNTRY", "KR"))
    p.add_argument("--interval", type=float, default=60.0, help="live: 상태 폴링 간격(초)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if args.replay:
        return run_replay(args)
    if args.live:
        return run_live(args)
    p.error("--replay 나 --live 중 하나가 필요하다")


if __name__ == "__main__":
    sys.exit(main())