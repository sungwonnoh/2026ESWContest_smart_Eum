import json

from t0_logger import CLIENT_ID, TOPIC, T0Store, run

ENV = {"version": 1, "sent_ts": 1790296800, "src_id": "rpi5"}


def meal(t0=1788217200.0, **over) -> dict:
    """Context Engine 가 hestia/log/t0 로 내보내는 모양 (envelope + T0Entry)."""
    base = {
        **ENV, "date": "2026-09-01", "type": "meal", "t0": t0, "source": "sensor",
        "prompted": False, "duration_sec": 1500.0, "area": None, "method": None,
        "confidence": None, "awake_areas": None, "eat_t0": t0 + 600 if isinstance(t0, (int, float)) else None,
    }
    return {**base, **over}


def lines(path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_appends_valid_payload(tmp_path):
    path = tmp_path / "t0_log.jsonl"
    store = T0Store(path)

    assert store.add(json.dumps(meal())) is True
    assert lines(path)[0]["type"] == "meal"
    assert lines(path)[0]["src_id"] == "rpi5"      # envelope 도 그대로 남는다


def test_duplicate_is_skipped(tmp_path):
    """QoS 1 재전달. 같은 식사가 두 번 학습되면 안 된다."""
    path = tmp_path / "t0_log.jsonl"
    store = T0Store(path)

    assert store.add(json.dumps(meal())) is True
    assert store.add(json.dumps(meal(sent_ts=1790296999))) is False
    assert len(lines(path)) == 1


def test_same_t0_different_type_is_kept(tmp_path):
    path = tmp_path / "t0_log.jsonl"
    store = T0Store(path)

    assert store.add(json.dumps(meal(1788217200.0))) is True
    assert store.add(json.dumps(meal(1788217200.0, type="hydration", eat_t0=None))) is True
    assert len(lines(path)) == 2


def test_duplicates_survive_restart(tmp_path):
    path = tmp_path / "t0_log.jsonl"
    T0Store(path).add(json.dumps(meal()))

    assert T0Store(path).add(json.dumps(meal())) is False
    assert len(lines(path)) == 1


def test_invalid_payload_is_dropped(tmp_path):
    path = tmp_path / "t0_log.jsonl"
    store = T0Store(path)

    assert store.add(b"not json") is False
    assert store.add(json.dumps({"type": "meal"})) is False        # 필수 필드 없음
    assert store.add(json.dumps(meal("어제"))) is False   # 숫자가 아닌 t0
    assert not path.exists()


def test_sleep_events_are_accepted(tmp_path):
    path = tmp_path / "t0_log.jsonl"
    store = T0Store(path)
    start = meal(1788271200.0, type="sleep_start", area="bedroom", method="banner",
                 confidence=0.9, awake_areas=["living"], eat_t0=None, duration_sec=0.0)
    end = meal(1788303600.0, type="sleep_end", eat_t0=None, duration_sec=0.0)

    assert store.add(json.dumps(start)) is True
    assert store.add(json.dumps(end)) is True


class FakeClient:
    def __init__(self):
        self.subscribed = []
        self.connected_to = None
        self.on_connect = None
        self.on_message = None

    def subscribe(self, topic, qos=0):
        self.subscribed.append((topic, qos))

    def connect_async(self, host, port, keepalive):
        self.connected_to = (host, port)


class Msg:
    def __init__(self, payload):
        self.payload = payload


def test_run_subscribes_qos1_and_stores(tmp_path):
    path = tmp_path / "t0_log.jsonl"
    client = FakeClient()
    run(T0Store(path), "rpi5.local", 1883, client_factory=lambda: client)

    assert client.connected_to == ("rpi5.local", 1883)
    client.on_connect(client, None, None, 0)
    assert client.subscribed == [(TOPIC, 1)]

    client.on_message(client, None, Msg(json.dumps(meal()).encode()))
    assert len(lines(path)) == 1


def test_client_id_is_fixed():
    """세션을 이어 받으려면 id 가 재시작해도 같아야 한다."""
    assert CLIENT_ID == "hestia-t0-logger"
