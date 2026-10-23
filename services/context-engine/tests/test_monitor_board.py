"""모니터 board 창의 청소 예약 표시 (tools/monitor.py).

엔진은 수면 중 청소 보류를 로그로만 남긴다. board 는 시뮬레이터가 넣은
예약 시각(state.schedule)이 된 순간의 활동이 SLEEPING 이면 '청소 시작 보류' 를
스스로 보여준다. 청소가 시작되면 청소 관련 문구는 띄우지 않는다.

예약 시각은 입력값 그대로다 — 테스트도 여러 시각으로 돌린다.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import monitor  # noqa: E402

DAY = datetime(2026, 10, 23)

# (예약, 예약을 넣는 시각) — 넣는 시각보다 예약이 앞서면 다음 날로 넘어간다
CASES = [
    ("00:00", DAY.replace(hour=22)),             # 오늘 이미 지남 → 다음 날 자정
    ("23:30", DAY.replace(hour=21)),             # 오늘 안
    ("07:15", DAY.replace(hour=20, minute=40)),  # 다음 날 아침
]


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t.timestamp()

    def __call__(self) -> float:
        return self.t

    def to(self, t: datetime) -> None:
        self.t = t.timestamp()


def due_of(sched: str, set_at: datetime) -> datetime:
    h, m = (int(x) for x in sched.split(":"))
    due = set_at.replace(hour=h, minute=m, second=0, microsecond=0)
    return due if due > set_at else due + timedelta(days=1)


@pytest.fixture
def clock(monkeypatch):
    c = Clock(DAY.replace(hour=12))
    monkeypatch.setattr(monitor, "now", c)
    return c


@pytest.fixture
def board(clock):
    inv = monitor.Inventory()
    inv.apply_registry([{"id": "vd-04", "device_type": "robot_cleaner", "area": "living"}])
    return monitor.BoardView(inv)


def cleaner(view, schedule=None, status="DOCKED") -> None:
    view.handle("hestia/device/vd-04/state",
                {"device_type": "robot_cleaner", "status": status, "schedule": schedule})


def activity(view, state: str) -> None:
    view.handle("hestia/context/activity", {"state": state})


def test_subscribes_only_cleaner_state_when_known(board):
    topics = board.topics()
    assert "hestia/device/vd-04/state" in topics
    assert "hestia/device/+/state" not in topics


def test_no_schedule_no_line(board):
    cleaner(board)
    assert "청소" not in board.render()


@pytest.mark.parametrize("sched,set_at", CASES)
def test_schedule_shows_until_due(board, clock, sched, set_at):
    clock.to(set_at)
    activity(board, "SLEEPING")                  # 넣을 때 자고 있어도 아직 보류가 아니다
    cleaner(board, sched)
    out = board.render()
    assert f"청소 예약 {sched}" in out
    assert "보류" not in out

    clock.to(due_of(sched, set_at) - timedelta(minutes=1))
    assert f"청소 예약 {sched}" in board.render()


@pytest.mark.parametrize("sched,set_at", CASES)
def test_asleep_at_due_shows_deferred(board, clock, sched, set_at):
    clock.to(set_at)
    cleaner(board, sched)
    activity(board, "SLEEPING")
    clock.to(due_of(sched, set_at))
    assert f"청소 시작 보류 (예약 {sched} · 수면 중)" in board.render()

    activity(board, "IN_BED_AWAKE")              # 깨도 청소가 시작될 때까지 보류 표시
    assert "청소 시작 보류" in board.render()


@pytest.mark.parametrize("sched,set_at", CASES)
def test_cleaning_hides_line_then_waits_next_day(board, clock, sched, set_at):
    clock.to(set_at)
    cleaner(board, sched)
    activity(board, "SLEEPING")
    due = due_of(sched, set_at)
    clock.to(due)
    board.render()

    clock.to(due + timedelta(minutes=5))
    cleaner(board, sched, status="CLEANING")
    assert "청소" not in board.render()          # 청소가 시작되면 청소 문구 없음

    clock.to(due + timedelta(minutes=40))
    cleaner(board, sched, status="DOCKED")       # 끝나면 다음 날 같은 시각 예약
    out = board.render()
    assert f"청소 예약 {sched}" in out
    assert "보류" not in out


@pytest.mark.parametrize("sched,set_at", CASES)
def test_awake_at_due_is_not_deferred(board, clock, sched, set_at):
    clock.to(set_at)
    cleaner(board, sched)
    activity(board, "WATCHING_TV")
    clock.to(due_of(sched, set_at))
    assert f"청소 예약 {sched} 도달" in board.render()

    activity(board, "SLEEPING")                  # 시각이 지난 뒤 잠든 것은 보류가 아니다
    assert "보류" not in board.render()


def test_changing_schedule_resets(board, clock):
    sched, set_at = CASES[0]
    clock.to(set_at)
    cleaner(board, sched)
    activity(board, "SLEEPING")
    clock.to(due_of(sched, set_at))
    board.render()

    cleaner(board, "09:30")
    out = board.render()
    assert "청소 예약 09:30" in out
    assert "보류" not in out
