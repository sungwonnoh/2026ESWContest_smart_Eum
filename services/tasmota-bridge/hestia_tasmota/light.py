"""조명 값 변환. 순수 함수 — 전구 없이 테스트된다.

    HESTIA                  Tasmota
    power "ON"/"OFF"        Power ON/OFF
    brightness 0..100       Dimmer 0..100          같은 범위다
    color_temp 켈빈         CT 153..500 (mireds)

mireds = 1000000 / 켈빈. 문서가 153(Cold White) ~ 500(Warm White) 이라
적고 있으므로 쓸 수 있는 구간은 2000K ~ 6536K 다. 밖은 클램프한다.
"""

from __future__ import annotations

from typing import Any

from .tasmota import TasmotaError, _load

CT_MIN = 153                 # Cold White
CT_MAX = 500                 # Warm White

LIGHT_POWER = frozenset({"ON", "OFF"})


# ------------------------------------------------------------ 색온도

def kelvin_to_mired(kelvin: int) -> int:
    if kelvin <= 0:
        raise ValueError(f"켈빈이 0 이하: {kelvin}")
    return max(CT_MIN, min(CT_MAX, round(1_000_000 / kelvin)))


def mired_to_kelvin(mired: int) -> int:
    if mired <= 0:
        raise ValueError(f"mired 가 0 이하: {mired}")
    return round(1_000_000 / mired)


# ------------------------------------------------------------ 내보내기

def speed_for(
    transition_ms: int | None, *, step_sec: float, max_speed: int
) -> int | None:
    """transition_ms → Tasmota Speed. 전환이 없으면 None.

    Speed 한 칸이 몇 초인지는 Tasmota 문서에서 확인하지 못했다. 그래서
    step_sec 과 max_speed 를 설정값으로 둔다 — 플러그가 오면 콘솔에서
    재어 맞춘다. 상한을 넘는 전환은 잘리고, 그 사실은 호출부가 로그로
    남긴다 (조용히 짧아지면 수면 프로브가 왜 빨리 끝나는지 모른다).
    """
    if not transition_ms or transition_ms <= 0:
        return None
    steps = round(transition_ms / 1000.0 / step_sec)
    return max(1, min(max_speed, int(steps)))


def build_backlog(params: dict[str, Any], *, speed: int | None = None) -> str | None:
    """set params → Backlog 한 줄. 보낼 게 없으면 None.

    순서가 중요하다. 전환 설정을 먼저 걸고, 색온도·밝기를 넣고, 전원을
    마지막에 둔다 — 꺼진 상태에서 Dimmer 를 주면 Tasmota 가 스스로
    켜기 때문에, 순서로 그 길을 막는다.

    OFF 일 때는 밝기·색온도를 함께 보내지 않는다. 끌 때 Dimmer 를
    주면 켜졌다 꺼지는 깜빡임이 된다.
    """
    power = params.get("power")
    if power is not None and power not in LIGHT_POWER:
        raise ValueError(f"power 가 ON/OFF 가 아님: {power!r}")
    on = power != "OFF"          # power 가 없으면 밝기만 바꾸는 명령이다

    body: list[str] = []
    if on:
        ct = params.get("color_temp")
        if ct is not None:
            body.append(f"CT {kelvin_to_mired(int(ct))}")
        bright = params.get("brightness")
        if bright is not None:
            body.append(f"Dimmer {max(0, min(100, int(bright)))}")
    if power is not None:
        body.append(f"Power {'ON' if on else 'OFF'}")

    if not body:
        return None

    head = [f"Fade {1 if speed else 0}"]
    if speed:
        head.append(f"Speed {speed}")
    return ";".join(head + body)


# ------------------------------------------------------------ 들여오기

def parse_result(raw: bytes | str) -> dict[str, Any]:
    """stat/RESULT · tele/STATE 에서 조명 필드만 꺼낸다.

    두 토픽의 필드 이름이 같아 같은 변환기를 태운다. 없는 키는 담지
    않는다 — '이번에 안 바뀜' 과 'OFF' 는 다르기 때문이다. 그래서
    돌려주는 dict 는 비어 있을 수 있다.

    CT 는 mireds 그대로 둔다. 켈빈 환산은 service 가 한다 — 내가 보낸
    값이 그대로 돌아왔는지 알아야 반올림 오차를 피할 수 있다.
    """
    doc = _load(raw)
    out: dict[str, Any] = {}

    power = doc.get("POWER", doc.get("POWER1"))     # 단일 채널은 POWER
    if power is not None:
        if not isinstance(power, str) or power.upper() not in LIGHT_POWER:
            raise TasmotaError(f"POWER 가 ON/OFF 가 아님: {power!r}")
        out["power"] = power.upper()

    dimmer = doc.get("Dimmer")
    if dimmer is not None:
        if isinstance(dimmer, bool) or not isinstance(dimmer, int):
            raise TasmotaError(f"Dimmer 가 정수가 아님: {dimmer!r}")
        if not 0 <= dimmer <= 100:
            raise TasmotaError(f"Dimmer 가 0..100 밖: {dimmer}")
        out["brightness"] = dimmer

    ct = doc.get("CT")
    if ct is not None:
        if isinstance(ct, bool) or not isinstance(ct, int):
            raise TasmotaError(f"CT 가 정수가 아님: {ct!r}")
        if not CT_MIN <= ct <= CT_MAX:
            raise TasmotaError(f"CT 가 {CT_MIN}..{CT_MAX} 밖: {ct}")
        out["mired"] = ct

    return out