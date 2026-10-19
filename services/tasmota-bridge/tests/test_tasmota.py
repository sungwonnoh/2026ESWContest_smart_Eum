"""실제 Tasmota 페이로드로 파서를 묶어둔다. 플러그가 없어도 돌아간다."""

import json

import pytest

from hestia_tasmota.tasmota import TasmotaError, parse_lwt, parse_power

# HLW8012 플러그가 실제로 보내는 모양
SENSOR = json.dumps({
    "Time": "2026-10-20T12:00:00",
    "ENERGY": {
        "TotalStartTime": "2026-10-19T09:00:00", "Total": 0.123,
        "Yesterday": 0.0, "Today": 0.123, "Period": 0,
        "Power": 913, "ApparentPower": 920, "ReactivePower": 100,
        "Factor": 0.99, "Voltage": 220, "Current": 4.18,
    },
})


def test_power_from_real_payload():
    assert parse_power(SENSOR) == 913.0


def test_power_accepts_bytes_and_float():
    assert parse_power(SENSOR.encode()) == 913.0
    assert parse_power('{"ENERGY":{"Power":12.5}}') == 12.5


def test_standby_is_zero_not_none():
    """대기 전력 0W 와 '전력 정보 없음' 은 다르다."""
    assert parse_power('{"ENERGY":{"Power":0}}') == 0.0


def test_no_energy_section_is_none():
    """온습도 센서만 붙은 플러그의 SENSOR 메시지."""
    payload = '{"Time":"2026-10-20T12:00:00","SI7021":{"Temperature":22.5}}'
    assert parse_power(payload) is None


@pytest.mark.parametrize("payload", [
    "not json",
    "[1,2,3]",
    '{"ENERGY":"913"}',
    '{"ENERGY":{}}',
    '{"ENERGY":{"Power":"913"}}',
    '{"ENERGY":{"Power":true}}',
    '{"ENERGY":{"Power":-5}}',
])
def test_broken_payloads_raise(payload):
    with pytest.raises(TasmotaError):
        parse_power(payload)


def test_lwt():
    assert parse_lwt(b"Online") is True
    assert parse_lwt("Offline") is False
    assert parse_lwt(" Online\n") is True
    with pytest.raises(TasmotaError):
        parse_lwt("whatever")