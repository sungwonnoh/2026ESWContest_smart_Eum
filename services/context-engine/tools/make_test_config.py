"""config/ 를 복사해 테스트용으로 시간값을 줄인다.

    python tools/make_test_config.py
    python tools/make_test_config.py --factor 60

엔진의 value() 는 DEMO 배수를 적용하지 않는다 — 시나리오 임계가
전부 거기로 읽히므로 프로파일만 바꿔서는 15분·4시간을 못 줄인다.
그래서 파일을 따로 만든다.

    python -m hestia_engine.runner --config ./config-test
    python tools/simulate.py --config ./config-test

목록에 없으면 기본 배율로 줄인다. 전에는 목록에 없는 키가 실제 값으로
남았고, 그러면 시나리오가 조용히 멈췄다 — eating_min_dwell_sec 이 300 인
채로 남아 EATING 이 영영 안 뜬 적이 있다. 경고도 안 났다. 새 타이머를
policy 에 추가해도 자동으로 따라오게 한다.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

DEFAULT_FACTOR = 30.0

# 배율로 줄이되 이보다 작아지지는 않는다. 0 이 되면 조건이 항상 참이 된다
FLOOR = {"sec": 5.0, "ms": 100.0}

# 키 = (원래값, 테스트값). 기본 배율이 안 맞는 것만 적는다.
# 원래값이 다르면 경고하고 기본 배율로 떨어진다 — 설정이 바뀐 걸 모르고
# 덮어쓰면 테스트가 엉뚱한 값으로 돈다.
REPLACEMENTS: list[tuple[str, str, str]] = [
    # --- 알림 총량. 이게 제일 중요하다.
    # 안 줄이면 알림 하나 보내고 30분간 나머지가 전부 막힌다.
    ("min_interval_sec", "1800", "20"),
    ("cooldown_sec", "1800", "30"),
    ("ack_deadline_sec", "600", "20"),
    ("ack_deadline_sec", "120", "30"), 
    ("expiry_sec", "3600", "300"),
    ("expiry_sec", "300", "60"),

    # comply 창은 "보냈으니 실제로 했는지 지켜본다" 이고 그동안 같은
    # 시나리오의 새 알림을 막는다. 분 단위라 _sec 치환에 안 걸렸었다.
    ("comply_window_min", "60", "2"),
    ("comply_window_min", "30", "1"),
    ("comply_window_min", "10", "1"),

    # --- 식사 묶음
    ("close_grace_sec", "600", "20"),
    ("min_duration_sec", "120", "15"),
    ("session_timeout_sec", "7200", "600"),
    ("cooking_lookback_sec", "7200", "600"),
    ("eating_min_dwell_sec", "300", "20"),
    ("prep_to_eating_gap_sec", "180", "5"),
    ("fridge_recent_window_sec", "900", "60"),

    # --- 수면 프로브
    ("probe_still_sec", "900", "30"),
    ("probe_still_sec_no_channel", "1800", "60"),
    ("probe_timeout_sec", "60", "30"),
    ("gap_after_awake_sec", "2700", "60"),
    ("probe_transition_ms", "30000", "3000"),

    # --- 수분. 4시간은 30배로 줄여도 8분이라 따로 누른다
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

    # --- 조명
    ("timeout_sec", "1800", "30"),              # 야간 조명 소등
    ("min_adjust_interval_sec", "60", "10"),    # 주간 밝기 한 단계
    ("manual_override_sec", "7200", "60"),      # 사용자가 손대면 물러남
]

# 줄이면 안 되는 것.
SKIP: dict[str, str] = {
    # 날씨 관측값의 유효 기간. 줄이면 특보가 금방 묵은 값이 되어 버려진다
    "max_age_sec": "날씨 신선도 — 줄이면 특보가 바로 묵는다",
}

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

# 시각대 제한. 저녁에 시연하면 주간 조명이 아예 안 돈다.
HOURS: dict[str, str] = {
    "day_start_hour": "0",
    "day_end_hour": "23",
}

LINE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)([0-9]+(?:\.[0-9]+)?)(\s*(?:#.*)?)$")


def unit_of(key: str) -> str | None:
    """시간값인지, 단위가 뭔지. 아니면 None.

    'min' 은 단위로 쓰지 않는다. on_min_w(최소 와트), moving_min(최소
    에너지), min_score, min_sample_days 처럼 '최소' 라는 뜻으로 훨씬
    자주 쓰여서, 분으로 읽으면 전력 임계 50W 가 2W 가 된다.
    분 단위 키는 REPLACEMENTS 에 직접 적는다 (comply_window_min).
    """
    tokens = key.split("_")
    if "sec" in tokens:
        return "sec"
    if "ms" in tokens:
        return "ms"
    return None


def scaled(value: str, unit: str, factor: float) -> str:
    try:
        v = float(value)
    except ValueError:
        return value
    if v <= 0:
        return value                      # 0 은 "없음" 이라 건드리지 않는다
    # 바닥값이 원래값보다 크면 그냥 둔다 — 줄이려다 늘리면 안 된다
    out = min(v, max(v / factor, FLOOR[unit]))
    if "." in value:
        places = len(value.split(".", 1)[1])
        return f"{out:.{places}f}"        # 0.05 를 0.1 로 반올림하지 않는다
    return str(int(round(out)))


def patch(text: str, factor: float) -> tuple[str, list[str]]:
    # (키, 원래값) → 테스트값
    exact: dict[tuple[str, str], str] = {(k, o): n for k, o, n in REPLACEMENTS}
    keyed: dict[str, list[str]] = {}
    for k, o, _ in REPLACEMENTS:
        keyed.setdefault(k, []).append(o)

    seen: set[tuple[str, str]] = set()
    notes: list[str] = []
    out: list[str] = []

    for line in text.splitlines():
        m = LINE.match(line)
        if m is None:
            out.append(line)
            continue

        indent, key, eq, value, tail = m.groups()

        if key in HOURS:
            new = HOURS[key]
            if new != value:
                notes.append(f"  시각대  {key}: {value} -> {new}")
            out.append(f"{indent}{key}{eq}{new}{tail}")
            continue

        if key in SKIP:
            notes.append(f"  유지    {key} = {value}  ({SKIP[key]})")
            out.append(line)
            continue

        # 명시 목록이 먼저다. 단위 판별(sec/ms)에 안 걸리는 키도 여기로 온다
        hit = exact.get((key, value))
        if hit is not None:
            seen.add((key, value))
            notes.append(f"  바꿈    {key}: {value} -> {hit}")
            out.append(f"{indent}{key}{eq}{hit}{tail}")
            continue

        unit = unit_of(key)
        if unit is None:
            # 0 은 "없음" 이라 줄일 것도 없다
            if key in keyed and float(value) > 0:
                # 분 단위처럼 자동 판별이 안 되는 키다. 목록에 값을 추가해야 한다
                notes.append(
                    f"  !! 못줄임 {key} = {value}"
                    f" (목록에 있는 값은 {', '.join(keyed[key])})"
                )
            out.append(line)               # 시간값이 아니다
            continue

        new = scaled(value, unit, factor)
        if new == value:
            out.append(line)               # 0 이거나 이미 바닥값
        elif key in keyed:
            # 같은 키가 섹션마다 다른 값을 갖는다 (notify.* 가 그렇다)
            notes.append(
                f"  다른값  {key}: {value} -> {new} (목록은 {', '.join(keyed[key])})"
            )
            out.append(f"{indent}{key}{eq}{new}{tail}")
        else:
            notes.append(f"  기본배율 {key}: {value} -> {new}")
            out.append(f"{indent}{key}{eq}{new}{tail}")

    patched = "\n".join(out)
    if text.endswith("\n"):
        patched += "\n"

    for old, new in QUIET_OFF:
        if old in patched:
            patched = patched.replace(old, new)
            notes.append(f"  정숙    {old}  ->  {new}")

    for key, old, _ in REPLACEMENTS:
        if (key, old) not in seen:
            notes.append(f"  없음    {key} = {old}")

    return patched, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="테스트용 설정 생성")
    ap.add_argument(
        "--factor", type=float, default=DEFAULT_FACTOR,
        help=f"목록에 없는 시간값을 1/N 로 줄인다 (기본 {DEFAULT_FACTOR:g})",
    )
    ap.add_argument("-y", "--yes", action="store_true", help="묻지 않고 덮어쓴다")
    args = ap.parse_args(argv)

    if args.factor <= 0:
        print("factor 는 0보다 커야 한다")
        return 1

    # tools/ 에서 올라간다: tools → context-engine → services → repo 루트
    root = Path(__file__).resolve().parents[3]
    src = root / "config"
    dst = root / "config-test"

    if not src.is_dir():
        print(f"config 디렉터리가 없음: {src}")
        return 1

    if dst.exists():
        if not args.yes:
            ans = input(f"{dst} 가 이미 있다. 지우고 다시 만들까? [y/N] ")
            if ans.strip().lower() != "y":
                return 0
        shutil.rmtree(dst)

    shutil.copytree(src, dst)

    policy = dst / "policy.toml"
    text = policy.read_text(encoding="utf-8")
    patched, notes = patch(text, args.factor)
    policy.write_text(patched, encoding="utf-8")

    print(f"{dst} 생성 (기본 배율 1/{args.factor:g})")
    for n in notes:
        print(n)

    warn = [n for n in notes if n.lstrip().startswith(("!!", "없음"))]
    if warn:
        print()
        print(f"확인 필요 {len(warn)}건 — policy.toml 이 바뀌었을 수 있다")

    print()
    print("실행:")
    print("  python -m hestia_engine.runner --config ./config-test")
    print("  python tools/simulate.py --config ./config-test")
    return 0


if __name__ == "__main__":
    sys.exit(main())