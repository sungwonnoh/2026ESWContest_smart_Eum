"""판단 화면: 근거 문장과 '최신 판단' 고르기."""

import time

import pytest

from app.services.context_service import factor_label, format_duration, to_factors
from conftest import envelope


def context(hestia, name, ts, **fields):
    payload = envelope("rpi5", **fields)
    payload["sent_ts"] = int(ts)
    assert hestia.receive(f"hestia/context/{name}", payload)


def latest(hestia):
    body = hestia.get("/explanations/latest").json()
    return body["contextName"], body["state"]


ACTIVITY = {"confidence": 0.9, "factors": {"hour": 9}}
PRESENCE = {"area": "living", "confidence": 0.8, "factors": {"vs-01_present": True}}


def test_future_virtual_clock_row_does_not_hide_new_judgment(hestia):
    """clock 으로 앞당긴 기록이 DB 에 남아도 엔진을 재시작한 뒤의 판단이 보인다."""
    now = time.time()
    context(hestia, "activity", now + 6 * 3600, state="EATING", **ACTIVITY)
    context(hestia, "activity", now, state="COOKING", **ACTIVITY)
    assert latest(hestia) == ("activity", "COOKING")


def test_republishing_same_state_does_not_take_over(hestia):
    """5분 주기 재발행은 상태 변화가 아니다."""
    now = time.time()
    context(hestia, "presence", now, **PRESENCE)
    context(hestia, "activity", now + 1, state="COOKING", **ACTIVITY)
    context(hestia, "presence", now + 2, **PRESENCE)
    assert latest(hestia) == ("activity", "COOKING")

    context(hestia, "presence", now + 3, **{**PRESENCE, "area": "kitchen"})
    assert latest(hestia) == ("presence", "kitchen")


def test_republish_refreshes_factors_of_same_judgment(hestia):
    now = time.time()
    context(hestia, "activity", now, state="COOKING", confidence=0.9, factors={"hour": 9})
    context(hestia, "activity", now + 1, state="COOKING", confidence=0.9, factors={"hour": 10})
    body = hestia.get("/explanations/latest").json()
    assert [f["label"] for f in body["factors"]] == ["시간대: 10시"]
    assert len(hestia.get("/explanations").json()) == 1


def test_sensor_factors_use_room_name_from_registry(hestia):
    hestia.receive("hestia/registry/devices", envelope("rpi5", devices=[
        {"virtual_id": "vs-01", "device_type": "presence", "area": "living"},
    ]))
    context(hestia, "presence", time.time(), area="living", confidence=0.95, factors={
        "vs-01_present": True, "vs-01_energy": 50, "vs-01_target": "BOTH", "vs-01_spread": 90,
    })
    factors = hestia.get("/explanations/latest").json()["factors"]
    assert [f["label"] for f in factors] == ["거실 재실 센서: 사람 감지"]


def test_engine_factors_read_as_sentences():
    """엔진이 실제로 보내는 값이다. 내부 계산값과 측정하지 못한 값은 보이지 않는다."""
    factors = to_factors({
        "concurrent_areas": 1,
        "mmwave_targets_max": None,
        "doorbell_recent": False,
        "tv_power": None,
        "tv_remote_sec": None,
        "nodes_online": 0,
        "top_score": 0.7,
        "score_share": 0.6,
        "hysteresis_on": "COOKING",
        "cooking_on": True,
        "fridge_recent": False,
        "meal_area_dwell_sec": 420,
    })
    assert [(f.label, f.satisfied) for f in factors] == [
        ("사람이 감지된 공간: 1곳", True),
        ("조리 기구 사용 중", True),
        ("최근 냉장고 사용 없음", False),
        ("식사 공간에 7분 머묾", True),
    ]
    assert factors[0].key == "concurrent_areas" and factors[0].value == 1


@pytest.mark.parametrize("key,value", [
    ("sensor_fault", True),
    ("below_min_score", True),
    ("vs-01_static_denied", True),
])
def test_warnings_are_not_shown_as_satisfied(key, value):
    assert to_factors({key: value})[0].satisfied is False


@pytest.mark.parametrize("key,value", [
    ("vs-01_energy", 50),
    ("vs-01_spread", 90),
    ("below_min_score", False),
    ("offline_sensors", []),
    ("unknown_new_factor", 1),
])
def test_hidden_factors(key, value):
    assert factor_label(key, value) is None


def test_sensor_without_registry_still_reads():
    assert factor_label("vs-99_occupied", False) == "압력 패드: 비어 있음"
    assert factor_label("vs-99_last_motion_sec", 90) == "마지막 움직임: 1분 전"


@pytest.mark.parametrize("seconds,text", [
    (0, "0초"), (59, "59초"), (60, "1분"), (3599, "59분"), (3600, "1시간"), (5400, "1시간 30분"),
])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text
