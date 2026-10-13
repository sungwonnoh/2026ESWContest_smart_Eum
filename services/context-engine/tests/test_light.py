"""DAY_LIGHT — 낮에 너무 어두우면 조명을 올린다.

'적정 밝기 유지' 가 아니라 '하한 보장' 이다. 시스템이 더한 몫만
되돌리므로 사용자가 맞춘 밝기 아래로는 내려가지 않는다.
"""

import pytest

from test_activity import MORNING, NIGHT
from test_sleep import Ctx

FLOOR = 300          # scenarios.day_light.floor_lux
CEILING = 600
STEP = 10
GAP = 60             # min_adjust_interval_sec
HOLD = 7200          # manual_override_sec

LUX = "vs-15"        # 거실 조도 센서
LIGHT = "vd-02"      # 거실 조명


@pytest.fixture
def c(tmp_path):
    return Ctx(tmp_path, start=MORNING)


def lux(c, value: float, seq: int = 1):
    return c.send(f"hestia/sensor/{LUX}/state", {
        "version": 1, "sent_ts": 0, "src_id": LUX, "seq": seq,
        "type": "light", "illuminance_lux": value,
    })


def living(c, *, brightness: int = 40, seq: int = 1):
    """거실에 사람이 있고 조명이 켜져 있다."""
    c.presence("vs-01", energy=45, seq=seq)
    c.light(LIGHT, power="ON", brightness=brightness, seq=seq)
    return c


def dl(c) -> tuple[dict, ...]:
    return tuple(p for p in c.cmds() if p.get("reason") == "DAY_LIGHT")


def test_raises_when_dark(c):
    """하한 아래면 한 단계 올린다."""
    living(c, brightness=40)
    lux(c, FLOOR - 50)
    c.engine.scenarios.tick(c.engine.context)

    assert dl(c)[-1]["params"]["brightness"] == 40 + STEP


def test_no_change_inside_deadband(c):
    """하한과 상한 사이에서는 아무것도 하지 않는다.

    데드밴드가 없으면 올린 결과가 상한을 넘어 바로 다시 내려가며
    발진한다.
    """
    living(c, brightness=40)
    lux(c, (FLOOR + CEILING) // 2)
    c.engine.scenarios.tick(c.engine.context)

    assert dl(c) == ()


def test_lowers_only_to_baseline(c):
    """사용자가 맞춘 40% 아래로는 내리지 않는다. 이 설계의 전부다."""
    living(c, brightness=40)
    lux(c, FLOOR - 50)
    c.engine.scenarios.tick(c.engine.context)
    assert dl(c)[-1]["params"]["brightness"] == 50

    # 기기가 반영을 보고하고 해가 들어온다 — 시계를 넘기기 전에
    c.light(LIGHT, power="ON", brightness=50, seq=2)
    lux(c, CEILING + 100, seq=2)
    c.at(MORNING + GAP + 5)
    assert dl(c)[-1]["params"]["brightness"] == 40      # 바닥

    # 더 밝아져도 더는 안 내린다
    c.light(LIGHT, power="ON", brightness=40, seq=3)
    lux(c, CEILING + 500, seq=3)
    before = len(dl(c))
    c.at(MORNING + GAP * 2 + 10)
    assert len(dl(c)) == before


def test_user_change_starts_hold(c):
    """사용자가 손대면 손을 뗀다. 되올리면 싸움이 된다."""
    living(c, brightness=40)
    lux(c, FLOOR - 50)
    c.engine.scenarios.tick(c.engine.context)
    before = len(dl(c))

    # 명령보다 늦은 시각이어야 되돌림으로 잡힌다
    c.at(MORNING + 10)
    c.light(LIGHT, power="ON", brightness=20, seq=2)
    assert len(dl(c)) == before

    # 유예 중에는 어두워져도 안 건드린다
    lux(c, FLOOR - 50, seq=2)
    c.at(MORNING + GAP + 5)
    assert len(dl(c)) == before


def test_frozen_in_evening(tmp_path):
    """저녁에는 동결한다. 어두워지면 사람이 직접 켜고,
    그 시간대에 조명을 올리는 것은 수면에 나쁘다."""
    c = Ctx(tmp_path, start=NIGHT)
    living(c, brightness=40)
    lux(c, FLOOR - 200)
    c.engine.scenarios.tick(c.engine.context)

    assert dl(c) == ()


def test_skips_when_area_empty(c):
    """아무도 없는 방을 밝힐 이유가 없다."""
    c.light(LIGHT, power="ON", brightness=40)
    lux(c, FLOOR - 50)
    c.engine.scenarios.tick(c.engine.context)

    assert dl(c) == ()