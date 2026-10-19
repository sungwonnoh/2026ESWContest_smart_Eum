"""Tasmota 페이로드 읽기. 순수 함수 — 실기기 없이 테스트된다.

    tele/<topic>/SENSOR   {"Time":"2026-10-20T12:00:00",
                           "ENERGY":{"Power":913,"Voltage":220,...}}
    tele/<topic>/LWT      Online | Offline        (평문, retained)

Tasmota 는 펌웨어라 플러그 모델이 달라도 이 형식이 같다. 에너지 측정 칩
(HLW8012 / BL0937 / CSE7766) 이 달라도 ENERGY.Power 는 공통이다 — 칩마다
다른 것은 ApparentPower 같은 부가 필드와 교정값이고, 교정은 Tasmota
콘솔(PowerSet)에서 하므로 브리지 밖이다.
"""

from __future__ import annotations

import json

ONLINE = "Online"
OFFLINE = "Offline"


class TasmotaError(ValueError):
    """페이로드가 Tasmota 형식이 아님."""


def parse_power(raw: bytes | str) -> float | None:
    """SENSOR 페이로드에서 와트를 꺼낸다.

    None 은 '이 메시지에 전력이 없다' 는 뜻이다 — Tasmota 는 붙어 있는
    센서를 한 SENSOR 메시지에 모아 보내므로, 온습도만 담긴 메시지가 올
    수 있다. 형식이 깨진 것과 구별해야 해서 예외를 쓰지 않는다.
    """
    doc = _load(raw)

    energy = doc.get("ENERGY")
    if energy is None:
        return None
    if not isinstance(energy, dict):
        raise TasmotaError(f"ENERGY 가 객체가 아님: {energy!r}")
    if "Power" not in energy:
        raise TasmotaError("ENERGY 에 Power 가 없음")

    watt = energy["Power"]
    if isinstance(watt, bool) or not isinstance(watt, (int, float)):
        raise TasmotaError(f"ENERGY.Power 가 숫자가 아님: {watt!r}")
    if watt < 0:
        raise TasmotaError(f"ENERGY.Power 가 음수: {watt!r}")
    return float(watt)


def parse_lwt(raw: bytes | str) -> bool:
    """LWT 페이로드. 살아 있으면 True. JSON 이 아니라 평문이다."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    text = text.strip()
    if text == ONLINE:
        return True
    if text == OFFLINE:
        return False
    raise TasmotaError(f"LWT 가 {ONLINE}/{OFFLINE} 이 아님: {text!r}")


def _load(raw: bytes | str) -> dict:
    try:
        doc = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TasmotaError(f"JSON 아님 — {exc}") from None
    if not isinstance(doc, dict):
        raise TasmotaError(f"객체가 아님: {type(doc).__name__}")
    return doc