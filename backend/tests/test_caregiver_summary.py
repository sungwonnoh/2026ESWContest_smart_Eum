from conftest import envelope
from datetime import date, timedelta


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
