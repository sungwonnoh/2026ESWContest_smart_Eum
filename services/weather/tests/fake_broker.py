"""실제 소켓으로 동작하는 작은 MQTT 3.1.1 브로커 (테스트 전용).

paho 의 연결·재연결·구독을 그대로 태우려고 쓴다. 필요한 패킷만 다룬다.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Published:
    topic: str
    payload: bytes
    qos: int
    retain: bool


class FakeBroker:
    def __init__(self, port: int = 0) -> None:
        self.port = port
        self.connects = 0
        self.subscribed: list[str] = []          # 지금 살아 있는 연결의 구독
        self.published: list[Published] = []
        self._server: socket.socket | None = None
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()

    def start(self) -> "FakeBroker":
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", self.port))
        server.listen()
        self.port = server.getsockname()[1]
        self._server = server
        threading.Thread(target=self._accept, args=(server,), daemon=True).start()
        return self

    def stop(self) -> None:
        """브로커가 죽은 것처럼 연결을 모두 끊고 포트를 닫는다."""
        server, self._server = self._server, None
        if server is not None:
            # accept 에 묶인 스레드까지 깨운다. close 만으로는 포트가 남을 수 있다
            try:
                server.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            server.close()
        with self._lock:
            clients, self._clients = self._clients, []
            self.subscribed = []
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()

    def send(self, topic: str, payload: bytes = b"{}", *, retain: bool = False) -> None:
        """구독자에게 메시지를 보낸다. retain=True 면 retained 로 받은 것처럼 간다."""
        name = topic.encode()
        body = len(name).to_bytes(2, "big") + name + payload
        packet = bytes([0x30 | (1 if retain else 0)]) + _remaining(len(body)) + body
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            client.sendall(packet)

    def wait_for(self, predicate, timeout: float = 8.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    # ------------------------------------------------------------ 내부

    def _accept(self, server: socket.socket) -> None:
        while True:
            try:
                client, _ = server.accept()
            except OSError:
                return
            with self._lock:
                self._clients.append(client)
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        try:
            while True:
                header = _read(client, 1)[0]
                body = _read(client, _read_remaining(client))
                if not self._handle(client, header, body):
                    return
        except OSError:
            pass

    def _handle(self, client: socket.socket, header: int, body: bytes) -> bool:
        kind = header >> 4
        if kind == 1:                            # CONNECT
            with self._lock:
                self.connects += 1
            client.sendall(b"\x20\x02\x00\x00")
        elif kind == 3:                          # PUBLISH
            qos = (header >> 1) & 3
            size = int.from_bytes(body[:2], "big")
            topic = body[2:2 + size].decode()
            offset = 2 + size + (2 if qos else 0)
            with self._lock:
                self.published.append(Published(topic, body[offset:], qos, bool(header & 1)))
            if qos == 1:
                client.sendall(b"\x40\x02" + body[2 + size:2 + size + 2])
        elif kind == 8:                          # SUBSCRIBE
            size = int.from_bytes(body[2:4], "big")
            with self._lock:
                self.subscribed.append(body[4:4 + size].decode())
            client.sendall(b"\x90\x03" + body[:2] + bytes([body[4 + size]]))
        elif kind == 12:                         # PINGREQ
            client.sendall(b"\xd0\x00")
        elif kind == 14:                         # DISCONNECT
            client.close()
            return False
        return True


def _read(client: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = client.recv(size - len(data))
        if not chunk:
            raise OSError("연결 끊김")
        data += chunk
    return data


def _read_remaining(client: socket.socket) -> int:
    value, shift = 0, 0
    while True:
        byte = _read(client, 1)[0]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value
        shift += 7


def _remaining(size: int) -> bytes:
    out = bytearray()
    while True:
        size, byte = size >> 7, size & 0x7F
        out.append(byte | (0x80 if size else 0))
        if not size:
            return bytes(out)
