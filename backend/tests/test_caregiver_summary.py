from conftest import envelope
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def day(date, meals, **extra):
    return envelope(
        "rpi5", date=date, meals=meals, hydrations=[], medications=[], **extra
    )


def test_day_context_is_stored_and_summary_can_be_requested(hestia):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    today = date.today().isoformat()
    assert hestia.receive("hestia/context/day", day(yesterday, [1, 2, 3]))
    assert hestia.receive("hestia/context/day", day(today, [1, 2]))

    response = hestia.post("/caregiver/summary", json={"days": 7})

    assert response.status_code == 200
    body = response.json()
    assert body["sent"] is False
    assert "식사: 5회" in body["text"]
    assert "하루 평균 2.5회" in body["text"]
    assert "수면 요약 원천 데이터" in body["text"]


def test_summary_includes_sleep_fields_when_context_engine_sends_them(hestia):
    assert hestia.receive(
        "hestia/context/day",
        day(date.today().isoformat(), [1, 2], sleep_interruptions=2, bathroom_visits=1),
    )

    response = hestia.post("/caregiver/summary", json={"days": 1})

    assert response.status_code == 200
    assert "평균 야간 각성 2.0회" in response.json()["text"]
    assert "평균 야간 화장실 1.0회" in response.json()["text"]


def test_summary_uses_completed_sleep_history_and_excludes_open_sleep(hestia):
    tz = ZoneInfo("Asia/Seoul")
    today = date.today()
    night_start = datetime.combine(today - timedelta(days=1), time(22), tzinfo=tz).timestamp()
    night_end = datetime.combine(today, time(6), tzinfo=tz).timestamp()
    nap_start = datetime.combine(today, time(13), tzinfo=tz).timestamp()
    nap_end = datetime.combine(today, time(14), tzinfo=tz).timestamp()
    open_start = datetime.combine(today, time(21), tzinfo=tz).timestamp()

    for ts, state in (
        (night_start, "SLEEPING"),
        (night_end, "AWAKE"),
        (nap_start, "SLEEPING"),
        (nap_end, "AWAKE"),
        (open_start, "SLEEPING"),
    ):
        assert hestia.receive(
            "hestia/context/activity",
            envelope("rpi5", sent_ts=ts, state=state),
        )

    response = hestia.post("/caregiver/summary", json={"days": 1})

    assert response.status_code == 200
    text = response.json()["text"]
    assert "야간 수면 1회 (총 8.0시간)" in text
    assert "낮잠 1회 (총 60분)" in text
