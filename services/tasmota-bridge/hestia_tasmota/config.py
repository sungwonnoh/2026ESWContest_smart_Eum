"""Tasmota 가 맡은 센서 읽기.

    config/homes/<name>.toml 의 [[sensors]] 중 source = "tasmota" 인 것

        [[sensors]]
        id = "vs-07"
        type = "power"
        source = "tasmota"

Context Engine 과 같은 파일을 읽지만, 브리지가 쓰는 것은 id 와 type 둘뿐이다.
area·roles·power_profile 은 엔진의 것이고, 임계 판정도 엔진이 한다.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:                                           # RPi 가 3.9 / 3.10 일 때
    import tomli as tomllib

SUPPORTED_TYPES = frozenset({"power"})


class ConfigError(ValueError):
    """설정이 없거나 잘못됨. 기동하지 않는다."""


@dataclass(frozen=True, slots=True)
class BridgeSensor:
    vid: str
    sensor_type: str


def load_sensors(home_path: Path) -> tuple[BridgeSensor, ...]:
    try:
        with home_path.open("rb") as fp:
            home = tomllib.load(fp)
    except FileNotFoundError:
        raise ConfigError(f"{home_path}: 파일 없음") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{home_path}: TOML 오류 — {exc}") from None

    raw = home.get("sensors")
    if not isinstance(raw, list):
        raise ConfigError(f"{home_path}: [[sensors]] 가 없음")
    return sensors_from(raw, home_path)


def sensors_from(
    raw: list[Any], where: Path | str = "[[sensors]]"
) -> tuple[BridgeSensor, ...]:
    out: list[BridgeSensor] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError(f"{where}: sensors[{i}] 가 테이블이 아님")
        if item.get("source") != "tasmota":
            continue
        if item.get("enabled") is False:
            continue

        vid = item.get("id")
        if not isinstance(vid, str) or not vid.strip():
            raise ConfigError(f"{where}: sensors[{i}].id 가 없거나 빈 문자열")
        stype = item.get("type")
        if stype not in SUPPORTED_TYPES:
            raise ConfigError(
                f"{where}: {vid} 의 type={stype!r} 은 브리지가 아직 모름 "
                f"(가능: {sorted(SUPPORTED_TYPES)})"
            )
        out.append(BridgeSensor(vid=vid.strip(), sensor_type=stype))

    seen = [s.vid for s in out]
    dup = sorted({v for v in seen if seen.count(v) > 1})
    if dup:
        raise ConfigError(f"{where}: 중복 id {dup}")
    return tuple(out)


SUPPORTED_DEVICE_TYPES = frozenset({"smart_light"})


@dataclass(frozen=True, slots=True)
class BridgeDevice:
    vid: str
    device_type: str


def load_devices(home_path: Path) -> tuple[BridgeDevice, ...]:
    try:
        with home_path.open("rb") as fp:
            home = tomllib.load(fp)
    except FileNotFoundError:
        raise ConfigError(f"{home_path}: 파일 없음") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{home_path}: TOML 오류 — {exc}") from None

    raw = home.get("devices")
    if not isinstance(raw, list):
        raise ConfigError(f"{home_path}: [[devices]] 가 없음")
    return devices_from(raw, home_path)


def devices_from(
    raw: list[Any], where: Path | str = "[[devices]]"
) -> tuple[BridgeDevice, ...]:
    out: list[BridgeDevice] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError(f"{where}: devices[{i}] 가 테이블이 아님")
        if item.get("source") != "tasmota":
            continue
        if item.get("enabled") is False:
            continue

        vid = item.get("id")
        if not isinstance(vid, str) or not vid.strip():
            raise ConfigError(f"{where}: devices[{i}].id 가 없거나 빈 문자열")
        dtype = item.get("device_type")
        if dtype not in SUPPORTED_DEVICE_TYPES:
            raise ConfigError(
                f"{where}: {vid} 의 device_type={dtype!r} 은 브리지가 아직 모름 "
                f"(가능: {sorted(SUPPORTED_DEVICE_TYPES)})"
            )
        out.append(BridgeDevice(vid=vid.strip(), device_type=dtype))

    seen = [d.vid for d in out]
    dup = sorted({v for v in seen if seen.count(v) > 1})
    if dup:
        raise ConfigError(f"{where}: 중복 id {dup}")
    return tuple(out)