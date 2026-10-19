import pytest

from hestia_tasmota.light import (
    build_backlog, kelvin_to_mired, mired_to_kelvin, parse_result, speed_for,
)
from hestia_tasmota.tasmota import TasmotaError


# ------------------------------------------------------------ 색온도

def test_kelvin_mired_round_trip_loses_precision():
    """이 오차가 _key_reverted 를 속인다 — service 가 막는다."""
    assert kelvin_to_mired(2700) == 370
    assert mired_to_kelvin(370) == 2703      # 2700 이 아니다


def test_kelvin_clamped_to_tasmota_range():
    assert kelvin_to_mired(10_000) == 153    # 더 차가워질 수 없다
    assert kelvin_to_mired(1_000) == 500     # 더 따뜻해질 수 없다


# ------------------------------------------------------------ 내보내기

def test_backlog_order_puts_power_last():
    line = build_backlog({"power": "ON", "brightness": 50, "color_temp": 2700})
    assert line == "Fade 0;CT 370;Dimmer 50;Power ON"


def test_backlog_with_transition():
    line = build_backlog({"power": "ON", "brightness": 30}, speed=8)
    assert line == "Fade 1;Speed 8;Dimmer 30;Power ON"


def test_off_does_not_carry_brightness():
    """끌 때 Dimmer 를 주면 켜졌다 꺼지는 깜빡임이 된다."""
    assert build_backlog({"power": "OFF", "brightness": 0}) == "Fade 0;Power OFF"


def test_brightness_only_keeps_light_on():
    assert build_backlog({"brightness": 70}) == "Fade 0;Dimmer 70"


def test_nothing_to_send():
    assert build_backlog({}) is None
    assert build_backlog({"transition_ms": 1000}) is None


def test_bad_power_refused():
    with pytest.raises(ValueError):
        build_backlog({"power": "STANDBY"})


# ------------------------------------------------------------ Speed

def test_speed_from_transition():
    assert speed_for(None, step_sec=0.5, max_speed=40) is None
    assert speed_for(0, step_sec=0.5, max_speed=40) is None
    assert speed_for(4000, step_sec=0.5, max_speed=40) == 8
    assert speed_for(100, step_sec=0.5, max_speed=40) == 1      # 0 이 되지 않는다


def test_speed_clamped_at_max():
    """수면 프로브의 30초가 상한에 걸리는지 — 플러그로 재어 확정한다."""
    assert speed_for(30_000, step_sec=0.5, max_speed=40) == 40


# ------------------------------------------------------------ 들여오기

def test_parse_result_picks_light_fields():
    raw = '{"POWER":"ON","Dimmer":50,"CT":370,"Color":"0,0,0"}'
    assert parse_result(raw) == {"power": "ON", "brightness": 50, "mired": 370}


def test_parse_result_partial():
    """Tasmota 는 바뀐 필드만 보낸다 — 없는 키를 지어내지 않는다."""
    assert parse_result('{"Dimmer":30}') == {"brightness": 30}


def test_parse_state_without_light_fields_is_empty():
    raw = '{"Time":"2026-10-20T12:00:00","Wifi":{"RSSI":70}}'
    assert parse_result(raw) == {}


@pytest.mark.parametrize("raw", [
    '{"POWER":"MAYBE"}',
    '{"Dimmer":101}',
    '{"Dimmer":"50"}',
    '{"CT":600}',
])
def test_broken_light_payloads_raise(raw):
    with pytest.raises(TasmotaError):
        parse_result(raw)