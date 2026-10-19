import pytest

from hestia_tasmota.config import BridgeDevice, BridgeSensor, ConfigError, sensors_from
from hestia_tasmota.service import BridgeService

SENSORS = (BridgeSensor("vs-07", "power"),)
DEVICES = (BridgeDevice("vd-02", "smart_light"),)

@pytest.fixture
def svc():
    return BridgeService(SENSORS)

@pytest.fixture
def lights():
    return BridgeService((), DEVICES)


# ------------------------------------------------------------ 설정

def test_only_tasmota_sensors():
    raw = [
        {"id": "vs-06", "type": "power", "source": "esp32"},
        {"id": "vs-07", "type": "power", "source": "tasmota"},
        {"id": "vs-08", "type": "presence", "source": "mock"},
    ]
    assert sensors_from(raw) == (BridgeSensor("vs-07", "power"),)


def test_disabled_is_skipped():
    raw = [{"id": "vs-07", "type": "power", "source": "tasmota", "enabled": False}]
    assert sensors_from(raw) == ()


def test_unsupported_type_refuses_to_start():
    raw = [{"id": "vs-03l", "type": "light", "source": "tasmota"}]
    with pytest.raises(ConfigError, match="아직 모름"):
        sensors_from(raw)


# ------------------------------------------------------------ 토픽

def test_round_trip(svc):
    assert "tele/hestia-vs-07/SENSOR" in svc.subscriptions()
    assert svc.vid_for("tele/hestia-vs-07/SENSOR") == ("vs-07", "/SENSOR")
    assert svc.vid_for("tele/hestia-vs-07/LWT") == ("vs-07", "/LWT")
    assert svc.state_topic("vs-07") == "hestia/sensor/vs-07/state"


@pytest.mark.parametrize("topic", [
    "tele/hestia-vs-99/SENSOR",      # 설정에 없는 기기
    "tele/tasmota_A1B2C3/SENSOR",    # 접두어가 다름
    "tele/hestia-vs-07/STATE",       # 전력이 없는 토픽
    "stat/hestia-vs-07/RESULT",
])
def test_unknown_topics_ignored(svc, topic):
    assert svc.vid_for(topic) is None

def test_device_topics(lights):
    subs = lights.device_subscriptions()
    assert "stat/hestia-vd-02/RESULT" in subs
    assert "tele/hestia-vd-02/STATE" in subs
    assert "hestia/device/vd-02/cmd" in subs
    assert lights.cmd_vid("hestia/device/vd-02/cmd") == "vd-02"
    assert lights.cmd_vid("hestia/device/vd-99/cmd") is None
    assert lights.result_vid("stat/hestia-vd-02/RESULT") == "vd-02"
    assert lights.cmnd_topic("vd-02") == "cmnd/hestia-vd-02/Backlog"


# ------------------------------------------------------------ 엔벨로프

def test_payload_shape(svc):
    p = svc.power_payload("vs-07", 913.0, 1_761_000_000.0)
    assert p == {
        "version": 1,
        "sent_ts": 1_761_000_000,
        "src_id": "vs-07",            # check_envelope 가 토픽과 비교한다
        "seq": 1,
        "type": "power",
        "watt": 913.0,
    }
    # state 는 넣지 않는다 — 임계 판정은 엔진의 [power.*] 가 한다
    assert "state" not in p


def test_seq_increases_per_device(svc):
    assert svc.power_payload("vs-07", 1.0, 0.0)["seq"] == 1
    assert svc.power_payload("vs-07", 2.0, 0.0)["seq"] == 2


def test_clock_offset_follows_sim(svc):
    assert svc.set_clock_offset(1800.0) is True
    assert svc.set_clock_offset(1800.0) is False      # retained 재수신
    assert svc.power_payload("vs-07", 1.0, 1000.0)["sent_ts"] == 2800


# ------------------------------------------------------------ 생존

def test_lwt_first_message_is_not_a_change(svc):
    assert svc.mark_online("vs-07", True) is False
    assert svc.mark_online("vs-07", False) is True
    assert svc.is_online("vs-07") is False

# 조명

def test_no_state_until_power_is_known(lights):
    """power 는 필수다 — 밝기만 알고는 state 를 만들 수 없다."""
    assert lights.apply_result("vd-02", {"brightness": 50}) is None
    assert lights.apply_result("vd-02", {"power": "ON"}) == {
        "power": "ON", "brightness": 50,
    }


def test_sent_kelvin_survives_round_trip(lights):
    """2700 을 보냈는데 2703 이 돌아오면 엔진이 되돌림으로 읽는다."""
    lights.note_sent("vd-02", {"color_temp": 2700}, 370)
    state = lights.apply_result("vd-02", {"power": "ON", "mired": 370})
    assert state["color_temp"] == 2700          # 2703 이 아니다


def test_manual_change_is_converted(lights):
    """사람이 앱에서 돌린 색온도는 내가 보낸 값이 아니므로 환산한다."""
    lights.note_sent("vd-02", {"color_temp": 2700}, 370)
    state = lights.apply_result("vd-02", {"power": "ON", "mired": 250})
    assert state["color_temp"] == 4000


def test_device_payload_has_type_and_source(lights):
    lights.apply_result("vd-02", {"power": "ON"})
    p = lights.device_payload("vd-02", {"power": "ON"}, 1_761_000_000.0)
    assert p["device_type"] == "smart_light"
    assert p["source"] == "tasmota"
    assert p["src_id"] == "vd-02"