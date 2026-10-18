import json

from app.services.telegram_service import TelegramService


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.body).encode()


def test_disabled_telegram_does_not_call_transport():
    calls = []
    service = TelegramService(False, "token", "chat", opener=lambda *args, **kwargs: calls.append(args))
    result = service.send_message("hello")
    assert not result.sent
    assert result.reason == "disabled"
    assert calls == []


def test_send_message_uses_configured_chat_id():
    calls = []

    def opener(req, timeout):
        calls.append((req, timeout))
        return Response({"ok": True, "result": {"message_id": 1}})

    service = TelegramService(True, "bot-token", "12345", timeout_sec=3, opener=opener)
    result = service.send_message("보호자 알림")
    assert result.sent
    req, timeout = calls[0]
    assert req.full_url.endswith("/botbot-token/sendMessage")
    assert json.loads(req.data.decode()) == {"chat_id": "12345", "text": "보호자 알림"}
    assert timeout == 3
