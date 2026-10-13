"""config/ 를 복사해 테스트용으로 시간값을 줄인다.

    python tools/make_test_config.py

엔진의 value() 는 DEMO 배수를 적용하지 않는다 — 시나리오 임계가
전부 거기로 읽히므로 프로파일만 바꿔서는 15분·4시간을 못 줄인다.
그래서 파일을 따로 만든다.

    python -m hestia_engine.runner --config ./config-test
    python tools/simulate.py --config ./config-test
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

# 키 = (원래값, 테스트값). 원래값이 다르면 건너뛰고 경고한다 —
# 설정이 바뀐 걸 모르고 덮어쓰면 테스트가 엉뚱한 값으로 돈다.
REPLACEMENTS: list[tuple[str, str, str]] = [
    # --- 알림 총량. 이게 제일 중요하다.
    # 안 줄이면 알림 하나 보내고 30분간 나머지가 전부 막힌다.
    ("min_interval_sec", "1800", "20"),
    ("cooldown_sec", "1800", "30"),
    ("ack_deadline_sec", "600", "20"),
    ("expiry_sec", "3600", "300"),

    # --- 식사 묶음
    ("close_grace_sec", "600", "20"),
    ("min_duration_sec", "120", "15"),
    ("session_timeout_sec", "7200", "600"),
    ("cooking_lookback_sec", "7200", "600"),

    # --- 수면 프로브
    ("probe_still_sec", "900", "30"),
    ("probe_still_sec_no_channel", "1800", "60"),
    ("probe_timeout_sec", "60", "10"),
    ("gap_after_awake_sec", "2700", "60"),
    ("probe_transition_ms", "30000", "3000"),

    # --- 수분
    ("gap_sec", "14400", "60"),
    ("short_gap_sec", "10800", "45"),
    ("sleep_end_gap_sec", "7200", "60"),
    ("renotify_sec", "7200", "60"),
    ("return_min_away_sec", "7200", "30"),
    ("return_delay_sec", "600", "20"),

    # --- 낙상
    ("still_sec", "900", "30"),
    ("inactivity_sec", "10800", "600"),

    # --- 부재 판정
    ("door_exit_quiet_sec", "600", "20"),
    ("no_motion_unknown_sec", "3600", "300"),
    ("door_exit_window_sec", "1800", "300"),

    # --- 재실 전환
    ("area_switch_min_sec", "20", "5"),
    ("mmwave_absent_grace_sec", "30", "10"),
    ("pir_absent_timeout_sec", "300", "60"),

    # --- 다인 판정
    ("multi_min_sec", "120", "20"),

    # --- 재처방
    ("retry_sec", "1800", "30"),

    # --- 야간 조명
    ("timeout_sec", "1800", "30"),
]

# 정숙 시간대. 밤에 테스트하면 식사·수분 알림이 통째로 막힌다.
QUIET_OFF: list[tuple[str, str]] = [
    ('pause_start = "22:00"', 'pause_start = "23:59"'),
    ('pause_end = "06:00"', 'pause_end = "00:01"'),
    ('pause_start = "21:00"', 'pause_start = "23:59"'),
    ('pause_end = "05:00"', 'pause_end = "00:01"'),
    (
        'quiet_hours = { start = "22:00", end = "07:00" }',
        'quiet_hours = { start = "23:59", end = "00:01" }',
    ),
]


def patch(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []

    for key, old, new in REPLACEMENTS:
        # 줄 머리에서만 찾는다. probe_still_sec 가
        # probe_still_sec_no_channel 을 먹지 않게 = 까지 포함한다.
        pattern = rf"^(\s*{re.escape(key)}\s*=\s*){re.escape(old)}\b"
        hit = re.search(pattern, text, flags=re.MULTILINE)
        if hit is None:
            cur = re.search(rf"^\s*{re.escape(key)}\s*=\s*(\S+)", text, re.MULTILINE)
            if cur is None:
                notes.append(f"  없음    {key}")
            else:
                notes.append(f"  건너뜀  {key} = {cur.group(1)} (기대 {old})")
            continue
        text = re.sub(pattern, rf"\g<1>{new}", text, flags=re.MULTILINE)
        notes.append(f"  바꿈    {key}: {old} -> {new}")

    for old, new in QUIET_OFF:
        if old in text:
            text = text.replace(old, new)
            notes.append(f"  바꿈    {old}  ->  {new}")

    return text, notes


def main() -> int:
    # tools/ 에서 올라간다: tools → context-engine → services → repo 루트
    root = Path(__file__).resolve().parents[3]
    src = root / "config"
    dst = root / "config-test"

    if not src.is_dir():
        print(f"config 디렉터리가 없음: {src}")
        return 1

    if dst.exists():
        ans = input(f"{dst} 가 이미 있다. 지우고 다시 만들까? [y/N] ")
        if ans.strip().lower() != "y":
            return 0
        shutil.rmtree(dst)

    shutil.copytree(src, dst)

    policy = dst / "policy.toml"
    text = policy.read_text(encoding="utf-8")
    patched, notes = patch(text)
    policy.write_text(patched, encoding="utf-8")

    print(f"{dst} 생성")
    for n in notes:
        print(n)
    print()
    print("실행:")
    print("  python -m hestia_engine.runner --config ./config-test")
    print("  python tools/simulate.py --config ./config-test")
    return 0


if __name__ == "__main__":
    sys.exit(main())