"""ThinQ Connect 응답 ↔ HESTIA 스키마.

순수 변환만 한다. 네트워크도 MQTT도 모른다 — 그래서 실물 가전 없이
공식 문서의 응답으로 검증할 수 있고, tests/test_device.py 가 그걸 한다.

속성 이름은 ThinQ Connect Open API 의 기기 프로필을 그대로 따른다.
    https://thinq.developer.lge.com/en/cloud/docs/thinq-connect/device-profile/air-purifier/
공식 SDK(thinqconnect 1.0.14) 의 AirPurifierProfile 이 쓰는 이름과 같다.

입력은 **봉투를 벗긴 상태 dict** 다. SDK 의 async_request 가
payload["response"] 만 돌려주므로(thinq_api.py:331), async_get_device_status()
가 주는 모양이 이것이다. 문서의 예시 응답에서 바깥 봉투를 뺀 것과 같다.
"""

from __future__ import annotations

from typing import Any, Mapping


class ThinqError(Exception):
    """옮길 수 없는 응답. 부르는 쪽이 로그만 남기고 넘어간다."""


# ── 전원 ────────────────────────────────────────────────────────────
# operation.airPurifierOperationMode
POWER_IN = {"POWER_ON": "ON", "POWER_OFF": "OFF"}
POWER_OUT = {"ON": "POWER_ON", "OFF": "POWER_OFF"}

# ── 운전 모드 ───────────────────────────────────────────────────────
# 엔진은 셋만 받는다 (messages.py PURIFIER_MODE = AUTO/SILENT/TURBO).
# ThinQ 는 운전모드 다섯(CLEAN·SLEEP·SILENT·AUTO·AIRCLEAN)과 풍량
# 여섯(LOW·MID·HIGH·POWER·AUTO·SLOW)으로 말한다. 그 둘을 셋으로 좁히는
# 것이 이 브리지의 일이다 — 좁히는 규칙을 코드 한 곳에 둔다.
SILENT_JOBS = frozenset({"SLEEP", "SILENT"})
TURBO_WINDS = frozenset({"POWER"})


def _wind(air_flow: Mapping[str, Any]) -> str | None:
    """풍량. 공식 SDK 가 windStrengthDetail 을 먼저 보므로 같이 맞춘다
    (devices/air_purifier.py 의 _get_preferred_property_key)."""
    for key in ("windStrengthDetail", "windStrength"):
        value = air_flow.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _mode(job: str | None, wind: str | None) -> str | None:
    if wind in TURBO_WINDS:
        return "TURBO"                 # 풍량이 운전모드를 덮는다
    if job in SILENT_JOBS:
        return "SILENT"
    if job or wind:
        return "AUTO"
    return None                        # 아무 단서도 없으면 안 채운다


def air_purifier_to_hestia(status: Mapping[str, Any]) -> dict[str, Any]:
    """ThinQ 공기청정기 상태 → hestia/device/{vid}/state 의 필드.

    power 가 없으면 옮기지 않는다. AirPurifierState 가 power 를
    필수로 받으므로(messages.py:392) 반쪽 상태를 올리면 엔진이
    파싱에서 버린다 — 그러면 왜 안 들어왔는지 추적할 길이 없다.
    """
    op = (status.get("operation") or {}).get("airPurifierOperationMode")
    power = POWER_IN.get(str(op))
    if power is None:
        raise ThinqError(f"airPurifierOperationMode 를 읽을 수 없음: {op!r}")

    out: dict[str, Any] = {"power": power}

    mode = _mode(
        (status.get("airPurifierJobMode") or {}).get("currentJobMode"),
        _wind(status.get("airFlow") or {}),
    )
    if mode is not None:
        out["mode"] = mode

    air = status.get("airQualitySensor") or {}
    # PM2 가 PM2.5 다. 이름이 PM2 인 것은 ThinQ 쪽 표기다
    if isinstance(air.get("PM2"), (int, float)):
        out["pm25"] = int(air["PM2"])
    if isinstance(air.get("humidity"), (int, float)):
        out["humidity_pct"] = float(air["humidity"])

    # 온도는 채우지 않는다. ThinQ 공기청정기 프로필에 온도 속성이 없다 —
    # airQualitySensor 는 PM·냄새·습도·종합지수까지만 준다.
    # AirPurifierState.temperature_c 는 None 으로 남고, 실내 온도는
    # vs-03c(ESP32 DHT)가 든다. demo.toml 의 vd-12 주석("온습도 출처")은
    # 습도까지만 맞다.
    return out


def air_purifier_to_thinq(params: Mapping[str, Any]) -> dict[str, Any]:
    """hestia/device/{vid}/cmd 의 params → ThinQ 제어 본문.

    POST /devices/{device-id} 의 body 이고, SDK 로는
    async_post_device_control(device_id, payload) 의 payload 다.
    """
    body: dict[str, Any] = {}

    power = params.get("power")
    if power is not None:
        if power not in POWER_OUT:
            raise ThinqError(f"power 가 ON/OFF 가 아님: {power!r}")
        body["operation"] = {"airPurifierOperationMode": POWER_OUT[power]}

    mode = params.get("mode")
    if mode == "TURBO":
        body["airFlow"] = {"windStrength": "POWER"}
    elif mode == "SILENT":
        body["airPurifierJobMode"] = {"currentJobMode": "SLEEP"}
    elif mode == "AUTO":
        body["airFlow"] = {"windStrength": "AUTO"}
    elif mode is not None:
        raise ThinqError(f"모르는 mode: {mode!r}")

    if not body:
        # transition_ms 만 온 경우가 여기다. 공기청정기에 점진 변화가
        # 없으므로 조용히 버리지 않고 알린다
        raise ThinqError(f"옮길 값이 없음: {dict(params)}")
    return body


# 기기 종류를 늘리는 자리. SDK 에 cooktop·air_conditioner 등이 이미 있다
# (thinqconnect/devices/). 쿡탑을 붙이면 vd-13 이 실물 경로를 탄다.
TO_HESTIA = {"air_purifier": air_purifier_to_hestia}
TO_THINQ = {"air_purifier": air_purifier_to_thinq}