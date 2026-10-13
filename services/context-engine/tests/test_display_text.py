"""알림 문구가 8글자 4줄 디스플레이에 들어가는가.

제목 1줄 + 본문 3줄. 줄 나눔은 문구에 "\\n" 으로 박혀 있다.
"""

import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from hestia_engine.model import MealPeak
from hestia_engine.scenarios import DISPLAY_COLS, ScenarioRunner

POLICY = Path(__file__).resolve().parents[3] / "config" / "policy.toml"
MAX_LINES = 4
REASONS = ("LONG_GAP", "SLEEP_END", "HEAT_ONSET", "DRY_ONSET",
           "DAILY_SHORT", "RETURNED_HOME")


def check(title: str, text: str) -> None:
    lines = [title, *text.split("\n")]
    assert len(lines) <= MAX_LINES, lines
    for line in lines:
        assert 0 < len(line) <= DISPLAY_COLS, f"{line!r} ({len(line)}자)"


def runner(meds: bool = False) -> SimpleNamespace:
    """문구 함수가 self 에서 쓰는 것만 갖춘 대역."""
    return SimpleNamespace(
        _notify_value=lambda scenario, key, default: default,
        _after_meal_meds_for=lambda peak, today: [object()] if meds else [],
    )


def notify_tables() -> list[tuple[str, dict]]:
    with POLICY.open("rb") as f:
        table = tomllib.load(f)["notify"]
    return [(k, v) for k, v in table.items() if "title" in v]


@pytest.mark.parametrize("name,entry", notify_tables())
def test_policy_texts_fit(name, entry):
    """policy.toml 의 고정 문구. 에스컬레이션이 이 값을 다시 쓴다."""
    check(entry["title"], entry.get("text", "-"))


@pytest.mark.parametrize("reason", REASONS)
def test_hydration_text_fits(reason):
    check("수분 섭취", ScenarioRunner._hydration_text(runner(), reason))


@pytest.mark.parametrize("center", [0, 480, 725, 1139])
@pytest.mark.parametrize("meds", [False, True])
def test_meal_text_fits(center, meds):
    peak = MealPeak(center=center, from_=max(center - 60, 0), to=center + 60,
                    predictability=None, days_ratio=1.0, meals_per_day=1.0)
    check("식사", ScenarioRunner._meal_text(runner(meds), peak, "2026-10-13"))


@pytest.mark.parametrize("name", ["감기약", "혈압약", "비타민디오메가쓰리", "A" * 30])
def test_medication_text_fits(name):
    med = SimpleNamespace(name=name)
    check("복약 시간", ScenarioRunner._medication_text(None, med))


@pytest.mark.parametrize("names", [
    ["혈압약"], ["혈압약", "당뇨약"], ["비타민디오메가쓰리", "a", "b", "c"],
])
def test_refill_text_fits(names):
    due = [SimpleNamespace(name=n) for n in names]
    check("처방 안내", ScenarioRunner._refill_text(None, due))


def test_speech_is_longer_than_display():
    """음성은 화면보다 길고 친절하다. 길이 제한은 없다."""
    r = runner(meds=True)
    for reason in REASONS:
        speech = ScenarioRunner._hydration_speech(r, reason)
        assert len(speech) > len(ScenarioRunner._hydration_text(r, reason))
        assert "\n" not in speech          # TTS 가 줄바꿈에서 끊어 읽지 않게
