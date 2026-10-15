import argparse
import json
from datetime import datetime, timedelta, timezone

from baseline import add_input_arguments
from mqtt_publisher import PublishError
from run_learning import run

KST = timezone(timedelta(hours=9))


def args(path, **over):
    parser = argparse.ArgumentParser()
    add_input_arguments(parser)
    ns = parser.parse_args(["--t0-jsonl", str(path)] if path else [])
    ns.host, ns.port = "localhost", 1883
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def write_meals(path, days=10):
    base = datetime(2026, 9, 1, tzinfo=KST)
    with path.open("w", encoding="utf-8") as fp:
        for d in range(days):
            t = base + timedelta(days=d, hours=8, minutes=(d * 7) % 30)
            fp.write(json.dumps({
                "version": 1, "sent_ts": 0, "src_id": "rpi5",
                "date": t.strftime("%Y-%m-%d"), "type": "meal", "t0": t.timestamp(),
                "source": "sensor", "prompted": False, "duration_sec": 1500.0,
                "eat_t0": t.timestamp() + 600,
            }) + "\n")


class Recorder:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, payload, host, port):
        if self.error:
            raise self.error
        self.calls.append(payload)
        return payload


def test_publishes_model_from_t0(tmp_path):
    path = tmp_path / "t0_log.jsonl"
    write_meals(path)
    publish = Recorder()

    assert run(args(path), publish=publish) == 0
    assert len(publish.calls) == 1
    assert "meal_time" in publish.calls[0]["distributions"]


def test_missing_file_does_not_publish(tmp_path):
    """파일이 없을 때 기본 데이터(Aruba)로 학습해 발행하면 안 된다."""
    publish = Recorder()

    assert run(args(tmp_path / "none.jsonl"), publish=publish) == 0
    assert publish.calls == []


def test_empty_file_does_not_publish(tmp_path):
    path = tmp_path / "t0_log.jsonl"
    path.write_text("", encoding="utf-8")
    publish = Recorder()

    assert run(args(path), publish=publish) == 0
    assert publish.calls == []


def test_only_hydration_does_not_publish(tmp_path):
    """hydration 은 학습 대상이 아니라 표본이 0 이 된다."""
    path = tmp_path / "t0_log.jsonl"
    path.write_text(json.dumps({
        "date": "2026-09-01", "type": "hydration", "t0": 1788230000.0,
        "source": "sensor", "prompted": False, "duration_sec": 0.0,
    }) + "\n", encoding="utf-8")
    publish = Recorder()

    assert run(args(path), publish=publish) == 0
    assert publish.calls == []


def test_publish_failure_returns_nonzero(tmp_path):
    path = tmp_path / "t0_log.jsonl"
    write_meals(path)

    assert run(args(path), publish=Recorder(error=PublishError("PUBACK 없음"))) == 1
