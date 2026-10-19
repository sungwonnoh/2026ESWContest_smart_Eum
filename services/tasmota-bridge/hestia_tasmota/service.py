"""토픽 매핑, 엔벨로프, 가상 시계.

센서 쪽은 얇다 — ENERGY.Power 를 watt 로 옮기고 엔벨로프를 채운다.
state(OFF/STANDBY/ON) 는 넣지 않는다. messages.py 의 PowerMessage 가
"state 는 선택 — 없으면 RPi5 가 YAML 임계값으로 판정" 이라 적고 있고,
policy.toml 의 [power.microwave] 가 히스테리시스와 min_hold_sec 을 이미
갖고 있다. 여기서 또 판정하면 두 곳이 어긋난다.

가전 쪽은 왕복이다. cmd 를 Backlog 로 번역해 내보내고, 전구가 돌려준
응답을 다시 HESTIA state 로 옮긴다.

토픽 규칙:
    센서   tele/hestia-vs-07/SENSOR   →  hestia/sensor/vs-07/state
    가전   hestia/device/vd-02/cmd    →  cmnd/hestia-vd-02/Backlog
           stat/hestia-vd-02/RESULT   →  hestia/device/vd-02/state
           tele/hestia-vd-02/STATE    ↗
    생존   tele/hestia-<vid>/LWT         센서와 가전 둘 다

Tasmota 쪽 기본 토픽은 tasmota_<MAC 뒤 6자리> 라 미리 알 수 없지만, 콘솔에서
`Topic hestia-vs-07` 로 정할 수 있다. 그러면 매핑 표가 필요 없다 —
접두어와 vid 로 양방향이 계산된다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .config import BridgeDevice, BridgeSensor
from .light import mired_to_kelvin

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SENSOR_SUFFIX = "/SENSOR"
LWT_SUFFIX = "/LWT"
RESULT_SUFFIX = "/RESULT"
STATE_SUFFIX = "/STATE"


@dataclass
class LightShadow:
    """브리지가 들고 있는 전구의 마지막 모습.

    power 를 들고 있는 이유 — SmartLightState.power 는 필수(_one_of)인데
    Tasmota 의 RESULT 는 바뀐 필드만 담는다. 밝기만 바뀐 응답으로 state 를
    만들려면 마지막 전원을 알아야 한다.

    sent_mired / sent_kelvin 을 들고 있는 이유 — 켈빈 → mireds → 켈빈
    왕복은 반올림으로 값이 틀어진다(2700K → 370 → 2703K). control.py 의
    _key_reverted 는 float 비교라 그 3K 차이를 '사용자가 되돌렸다' 로
    읽는다. 내가 보낸 mireds 가 그대로 돌아오면 원래 켈빈을 되돌려 쓴다.
    """

    power: str | None = None
    brightness: int | None = None
    kelvin: int | None = None
    sent_mired: int | None = None
    sent_kelvin: int | None = None


class BridgeService:
    def __init__(
        self,
        sensors: tuple[BridgeSensor, ...],
        devices: tuple[BridgeDevice, ...] = (),
        *,
        topic_prefix: str = "hestia-",
    ) -> None:
        self._sensors = {s.vid: s for s in sensors}
        self._devices = {d.vid: d for d in devices}
        self._shadow: dict[str, LightShadow] = {d.vid: LightShadow() for d in devices}
        self._prefix = topic_prefix
        self._seq: dict[str, int] = {}
        self._offset: float = 0.0            # 시연용 가상 시계
        self._online: dict[str, bool] = {}

    # ============================================================ 토픽

    @property
    def vids(self) -> tuple[str, ...]:
        return tuple(self._sensors)

    @property
    def device_vids(self) -> tuple[str, ...]:
        return tuple(self._devices)

    def is_sensor(self, vid: str) -> bool:
        return vid in self._sensors

    def subscriptions(self) -> tuple[str, ...]:
        """센서 쪽 구독.

        와일드카드를 쓰지 않는다 — 설정에 없는 기기의 메시지를 받아
        vid 를 추측하는 길을 아예 두지 않기 위해서다.
        """
        out: list[str] = []
        for vid in self._sensors:
            base = f"tele/{self._prefix}{vid}"
            out.append(base + SENSOR_SUFFIX)
            out.append(base + LWT_SUFFIX)
        return tuple(out)

    def device_subscriptions(self) -> tuple[str, ...]:
        """가전 쪽 구독. Tasmota 응답 둘 + 생존 + 엔진의 cmd."""
        out: list[str] = []
        for vid in self._devices:
            out.append(f"stat/{self._prefix}{vid}{RESULT_SUFFIX}")
            out.append(f"tele/{self._prefix}{vid}{STATE_SUFFIX}")
            out.append(f"tele/{self._prefix}{vid}{LWT_SUFFIX}")
            out.append(f"hestia/device/{vid}/cmd")
        return tuple(out)

    def vid_for(self, topic: str) -> tuple[str, str] | None:
        """tele/<prefix><vid>/{SENSOR,LWT} → (vid, 접미어).

        SENSOR 는 센서만, LWT 는 센서와 가전 둘 다 — 전구가 죽어도
        브로커가 LWT 를 쓴다.
        """
        head = f"tele/{self._prefix}"
        if not topic.startswith(head):
            return None
        for suffix in (SENSOR_SUFFIX, LWT_SUFFIX):
            if not topic.endswith(suffix):
                continue
            vid = topic[len(head): -len(suffix)]
            if suffix == SENSOR_SUFFIX and vid in self._sensors:
                return vid, suffix
            if suffix == LWT_SUFFIX and (vid in self._sensors or vid in self._devices):
                return vid, suffix
        return None

    def cmd_vid(self, topic: str) -> str | None:
        """hestia/device/<vid>/cmd → vid."""
        if not topic.startswith("hestia/device/") or not topic.endswith("/cmd"):
            return None
        vid = topic[len("hestia/device/"): -len("/cmd")]
        return vid if vid in self._devices else None

    def result_vid(self, topic: str) -> str | None:
        """stat/<prefix><vid>/RESULT · tele/<prefix><vid>/STATE → vid."""
        for head, suffix in (
            (f"stat/{self._prefix}", RESULT_SUFFIX),
            (f"tele/{self._prefix}", STATE_SUFFIX),
        ):
            if topic.startswith(head) and topic.endswith(suffix):
                vid = topic[len(head): -len(suffix)]
                if vid in self._devices:
                    return vid
        return None

    @staticmethod
    def state_topic(vid: str) -> str:
        return f"hestia/sensor/{vid}/state"

    @staticmethod
    def device_state_topic(vid: str) -> str:
        return f"hestia/device/{vid}/state"

    def cmnd_topic(self, vid: str) -> str:
        return f"cmnd/{self._prefix}{vid}/Backlog"

    def query_topic(self, vid: str) -> str:
        """현재 상태를 물어보는 토픽. 빈 페이로드를 쏘면 STATE 가 온다."""
        return f"cmnd/{self._prefix}{vid}/State"

    # ============================================================ 가상 시계

    def set_clock_offset(self, offset: float) -> bool:
        """ESP32 의 onSimClock 과 같은 처신.

        sent_ts 가 엔진의 가상 시각과 어긋나면 엔진이 묵은 메시지로 본다.
        바뀌었을 때만 True — retained 재수신은 조용히 넘긴다.
        """
        if offset == self._offset:
            return False
        self._offset = offset
        return True

    def now(self, wall: float) -> float:
        return wall + self._offset

    # ============================================================ 센서

    def power_payload(self, vid: str, watt: float, wall: float) -> dict[str, Any]:
        self._seq[vid] = self._seq.get(vid, 0) + 1
        return {
            "version": SCHEMA_VERSION,
            "sent_ts": int(self.now(wall)),
            # check_envelope 가 토픽의 vid 와 비교한다 — 다르면 SchemaError
            "src_id": vid,
            "seq": self._seq[vid],
            "type": "power",
            "watt": round(float(watt), 1),
        }

    # ============================================================ 가전

    def note_sent(
        self, vid: str, params: dict[str, Any], mired: int | None
    ) -> None:
        """내보낸 명령을 기억한다. 색온도 왕복 오차를 막는 쪽이 핵심이다."""
        sh = self._shadow.get(vid)
        if sh is None or mired is None:
            return
        ct = params.get("color_temp")
        if ct is None:
            return
        sh.sent_mired = mired
        sh.sent_kelvin = int(ct)

    def apply_result(
        self, vid: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Tasmota 응답을 그림자에 반영하고 state 에 실을 dict 를 돌려준다.

        전원을 아직 한 번도 못 봤으면 None — power 가 필수라 state 를
        만들 수 없다. 이때는 호출부가 State 를 물어본다.
        """
        sh = self._shadow.get(vid)
        if sh is None:
            return None

        if "power" in fields:
            sh.power = fields["power"]
        if "brightness" in fields:
            sh.brightness = fields["brightness"]
        if "mired" in fields:
            mired = fields["mired"]
            if mired == sh.sent_mired and sh.sent_kelvin is not None:
                sh.kelvin = sh.sent_kelvin   # 내가 보낸 값 그대로 — 환산하지 않는다
            else:
                sh.kelvin = mired_to_kelvin(mired)

        if sh.power is None:
            return None

        out: dict[str, Any] = {"power": sh.power}
        if sh.brightness is not None:
            out["brightness"] = sh.brightness
        if sh.kelvin is not None:
            out["color_temp"] = sh.kelvin
        return out

    def device_payload(
        self, vid: str, fields: dict[str, Any], wall: float
    ) -> dict[str, Any]:
        self._seq[vid] = self._seq.get(vid, 0) + 1
        return {
            "version": SCHEMA_VERSION,
            "sent_ts": int(self.now(wall)),
            "src_id": vid,
            "seq": self._seq[vid],
            "device_type": self._devices[vid].device_type,
            "source": "tasmota",
            **fields,
        }

    # ============================================================ 생존

    def mark_online(self, vid: str, online: bool) -> bool:
        """LWT 수신. 값이 '바뀌었으면' True (첫 수신은 False)."""
        was = self._online.get(vid)
        self._online[vid] = online
        return was is not None and was != online

    def is_online(self, vid: str) -> bool:
        return self._online.get(vid, True)