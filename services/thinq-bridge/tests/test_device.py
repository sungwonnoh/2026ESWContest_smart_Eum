"""변환기 테스트. 실물 가전 없이 공식 문서의 응답으로 검증한다."""

import json
from pathlib import Path

import pytest

from hestia_thinq.device import (
    ThinqError,
    air_purifier_to_hestia,
    air_purifier_to_thinq,
)

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "air_purifier_status.json"


def test_sample_converts():
    """문서의 상태 응답이 그대로 엔진 필드가 된다 — 회귀 기준점."""
    status = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert air_purifier_to_hestia(status) == {
        "power": "ON",
        "mode": "AUTO",          # AIRCLEAN + windStrength AUTO
        "pm25": 9,               # PM2 가 PM2.5 다
        "humidity_pct": 30.0,
    }


def test_temperature_is_not_invented():
    """ThinQ 공기청정기 프로필에 온도가 없다. 만들어 채우지 않는다."""
    status = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert "temperature_c" not in air_purifier_to_hestia(status)


def test_power_off():
    out = air_purifier_to_hestia(
        {"operation": {"airPurifierOperationMode": "POWER_OFF"}}
    )
    assert out == {"power": "OFF"}


def test_unknown_power_is_refused():
    """반쪽 상태를 올리면 엔진이 파싱에서 버린다 — 여기서 막는다."""
    with pytest.raises(ThinqError):
        air_purifier_to_hestia({"operation": {"airPurifierOperationMode": "BOOT"}})
    with pytest.raises(ThinqError):
        air_purifier_to_hestia({})


@pytest.mark.parametrize(
    "job, wind, want",
    [
        ("AIRCLEAN", "AUTO", "AUTO"),
        ("CLEAN", "MID", "AUTO"),
        ("SLEEP", "LOW", "SILENT"),
        ("SILENT", "SLOW", "SILENT"),
        ("CLEAN", "POWER", "TURBO"),      # 풍량이 운전모드를 덮는다
        ("SLEEP", "POWER", "TURBO"),
    ],
)
def test_mode_narrowing(job, wind, want):
    """다섯 가지 운전모드와 여섯 가지 풍량을 엔진의 셋으로 좁힌다."""
    out = air_purifier_to_hestia({
        "operation": {"airPurifierOperationMode": "POWER_ON"},
        "airPurifierJobMode": {"currentJobMode": job},
        "airFlow": {"windStrength": wind},
    })
    assert out["mode"] == want


def test_wind_strength_detail_wins():
    """공식 SDK 가 windStrengthDetail 을 먼저 본다. 같이 맞춘다."""
    out = air_purifier_to_hestia({
        "operation": {"airPurifierOperationMode": "POWER_ON"},
        "airFlow": {"windStrengthDetail": "POWER", "windStrength": "LOW"},
    })
    assert out["mode"] == "TURBO"


def test_mode_absent_when_no_clue():
    out = air_purifier_to_hestia(
        {"operation": {"airPurifierOperationMode": "POWER_ON"}}
    )
    assert "mode" not in out


# ── 제어 방향 ────────────────────────────────────────────────────

def test_control_power():
    assert air_purifier_to_thinq({"power": "ON"}) == {
        "operation": {"airPurifierOperationMode": "POWER_ON"}
    }
    assert air_purifier_to_thinq({"power": "OFF"}) == {
        "operation": {"airPurifierOperationMode": "POWER_OFF"}
    }


@pytest.mark.parametrize(
    "mode, want",
    [
        ("TURBO", {"airFlow": {"windStrength": "POWER"}}),
        ("SILENT", {"airPurifierJobMode": {"currentJobMode": "SLEEP"}}),
        ("AUTO", {"airFlow": {"windStrength": "AUTO"}}),
    ],
)
def test_control_mode(mode, want):
    assert air_purifier_to_thinq({"mode": mode}) == want


def test_control_power_and_mode_together():
    assert air_purifier_to_thinq({"power": "ON", "mode": "TURBO"}) == {
        "operation": {"airPurifierOperationMode": "POWER_ON"},
        "airFlow": {"windStrength": "POWER"},
    }


def test_control_refuses_empty():
    with pytest.raises(ThinqError):
        air_purifier_to_thinq({})