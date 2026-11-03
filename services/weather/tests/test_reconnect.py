"""실제 paho 와 소켓으로 연결 생명주기를 확인한다. Raspberry Pi 는 필요 없다."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import FakeKma, kst, ncst_response
from fake_broker import FakeBroker
from hestia_weather import runner as runner_module
from hestia_weather.kma import KmaClient
from hestia_weather.runner import DEMO_TOPIC, GET_TOPIC, WEATHER_TOPIC, Runner
from hestia_weather.service import Location, WeatherService

HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = HERE.parent
REPO = HERE.parents[2]

NOW = kst(2026, 10, 10, 14, 20)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def weather_messages(broker: FakeBroker) -> list[dict]:
    return [json.loads(p.payload) for p in list(broker.published) if p.topic == WEATHER_TOPIC]


@pytest.fixture
def service_runner():
    """Runner 를 스레드에서 돌린다. 끝나면 정리까지 확인한다."""
    started: list[tuple[Runner, threading.Thread]] = []

    def launch(port: int) -> Runner:
        fake = FakeKma({"1400": ncst_response("20261010", "1400")})
        service = WeatherService(KmaClient("key", 60, 127, fetch=fake), Location("서울", 60, 127))
        runner = Runner(service, host="127.0.0.1", port=port, clock=lambda: NOW)
        thread = threading.Thread(target=runner.run, daemon=True)
        thread.start()
        started.append((runner, thread))
        return runner

    yield launch

    for runner, thread in started:
        runner.stop()
        thread.join(timeout=8)
        assert not thread.is_alive(), "stop 뒤에도 run 이 끝나지 않음"


@pytest.fixture
def broker():
    b = FakeBroker().start()
    yield b
    b.stop()


def test_connects_and_subscribes(broker, service_runner):
    service_runner(broker.port)

    assert broker.wait_for(lambda: set(broker.subscribed) == {GET_TOPIC, DEMO_TOPIC})
    assert broker.wait_for(lambda: weather_messages(broker))
    published = [p for p in broker.published if p.topic == WEATHER_TOPIC][0]
    assert (published.qos, published.retain) == (1, False)


def test_starts_while_broker_is_down_and_connects_later(service_runner):
    port = free_port()
    runner = service_runner(port)
    time.sleep(1.5)
    assert not runner.client.is_connected()

    broker = FakeBroker(port).start()
    try:
        assert broker.wait_for(lambda: set(broker.subscribed) == {GET_TOPIC, DEMO_TOPIC})
        assert broker.wait_for(lambda: weather_messages(broker))
    finally:
        broker.stop()


def test_outage_does_not_spin_and_recovery_needs_no_restart(service_runner):
    broker = FakeBroker().start()
    runner = service_runner(broker.port)
    assert broker.wait_for(lambda: weather_messages(broker))
    before = len(weather_messages(broker))
    broker.stop()
    assert broker.wait_for(lambda: not runner.client.is_connected())

    # 공회전이면 벽시계만큼 CPU 를 쓴다
    cpu, wall = time.process_time(), time.monotonic()
    time.sleep(3)
    cpu, wall = time.process_time() - cpu, time.monotonic() - wall
    assert cpu / wall < 0.2, f"끊긴 동안 CPU {cpu / wall:.0%}"

    broker.start()                               # 같은 포트로 다시 뜬다
    try:
        # 재구독과 캐시 재발행
        assert broker.wait_for(lambda: set(broker.subscribed) == {GET_TOPIC, DEMO_TOPIC}, timeout=35)
        assert broker.wait_for(lambda: len(weather_messages(broker)) > before)
        assert weather_messages(broker)[-1]["reason"] == "update"
        assert broker.connects == 2
    finally:
        broker.stop()


def test_api_polling_keeps_running_during_outage(service_runner):
    broker = FakeBroker().start()
    runner = service_runner(broker.port)
    assert broker.wait_for(lambda: weather_messages(broker))
    broker.stop()
    assert broker.wait_for(lambda: not runner.client.is_connected())

    ticks = []
    original = runner.tick
    runner.tick = lambda: (ticks.append(1), original())[1]
    time.sleep(1.5)
    assert len(ticks) >= 2                       # 조회 루프는 멈추지 않는다
    assert len(ticks) < 10                       # 그렇다고 돌지도 않는다


def test_get_gets_reply_and_retained_get_is_ignored(broker, service_runner):
    service_runner(broker.port)
    assert broker.wait_for(lambda: GET_TOPIC in broker.subscribed)
    assert broker.wait_for(lambda: weather_messages(broker))

    broker.send(GET_TOPIC, retain=True)
    time.sleep(1.0)
    assert [m["reason"] for m in weather_messages(broker)] == ["update"]

    broker.send(GET_TOPIC)
    assert broker.wait_for(lambda: weather_messages(broker)[-1]["reason"] == "reply")


def test_demo_command_reaches_service(broker, service_runner):
    service_runner(broker.port)
    assert broker.wait_for(lambda: DEMO_TOPIC in broker.subscribed)
    assert broker.wait_for(lambda: weather_messages(broker))

    broker.send(DEMO_TOPIC, json.dumps({"heat": True}).encode())
    assert broker.wait_for(lambda: weather_messages(broker)[-1].get("demo") is True)


def test_stop_cleans_up_network_thread(broker, service_runner):
    runner = service_runner(broker.port)
    assert broker.wait_for(lambda: GET_TOPIC in broker.subscribed)
    network_threads = lambda: [t for t in threading.enumerate() if t.name.startswith("paho-mqtt-client-")]
    assert network_threads()

    runner.stop()
    assert broker.wait_for(lambda: not runner.client.is_connected())
    assert broker.wait_for(lambda: not network_threads())


def test_stop_during_outage_returns_quickly():
    port = free_port()
    fake = FakeKma({"1400": ncst_response("20261010", "1400")})
    service = WeatherService(KmaClient("key", 60, 127, fetch=fake), Location("서울", 60, 127))
    runner = Runner(service, host="127.0.0.1", port=port, clock=lambda: NOW)
    thread = threading.Thread(target=runner.run, daemon=True)
    thread.start()
    time.sleep(1.5)

    started = time.monotonic()
    runner.stop()
    thread.join(timeout=8)
    assert not thread.is_alive()
    assert time.monotonic() - started < 5


SIGTERM_SCRIPT = """
import sys
sys.path.insert(0, {tests!r})
from conftest import FakeKma, kst, ncst_response
from hestia_weather import runner as r
from hestia_weather.kma import KmaClient

fake = FakeKma({{"1400": ncst_response("20261010", "1400")}})
real = r.KmaClient
r.KmaClient = lambda key, nx, ny: real(key, nx, ny, fetch=fake)
raise SystemExit(r.main(["--config", {config!r}, "--host", "127.0.0.1", "--port", {port!r}]))
"""


def test_sigterm_exits_cleanly(broker):
    script = SIGTERM_SCRIPT.format(tests=str(HERE), config=str(REPO / "config"), port=str(broker.port))
    env = {**os.environ, "KMA_SERVICE_KEY": "key", "PYTHONPATH": str(PACKAGE_ROOT)}
    process = subprocess.Popen([sys.executable, "-c", script], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert broker.wait_for(lambda: GET_TOPIC in broker.subscribed, timeout=15)
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=8)
    finally:
        process.kill()

    assert process.returncode == 0, output
    assert "종료" in output
    assert "Traceback" not in output


def test_once_does_not_touch_mqtt(monkeypatch, capsys):
    fake = FakeKma({"1400": ncst_response("20261010", "1400")})
    real = runner_module.KmaClient
    monkeypatch.setattr(runner_module, "KmaClient", lambda key, nx, ny: real(key, nx, ny, fetch=fake))
    monkeypatch.setattr(runner_module.time, "time", lambda: NOW)
    monkeypatch.setenv("KMA_SERVICE_KEY", "key")

    def forbidden(self):
        raise AssertionError("--once 인데 MQTT 를 시작함")

    monkeypatch.setattr(Runner, "start", forbidden)
    monkeypatch.setattr(Runner, "run", forbidden)

    port = free_port()
    code = runner_module.main(["--once", "--config", str(REPO / "config"),
                               "--host", "127.0.0.1", "--port", str(port)])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "update"
