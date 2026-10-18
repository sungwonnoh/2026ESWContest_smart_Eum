"""Telegram Bot API 전달 계층.

외부 Telegram SDK에 의존하지 않고 Bot API의 sendMessage만 사용한다.
토큰이 없는 개발 환경에서는 아무 것도 전송하지 않는다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib import request

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramResult:
    sent: bool
    reason: str = ""


class TelegramService:
    def __init__(
        self,
        enabled: bool,
        bot_token: str,
        chat_id: str,
        timeout_sec: float = 10.0,
        opener=request.urlopen,
    ) -> None:
        self._enabled = enabled
        self._bot_token = bot_token.strip()
        self._chat_id = chat_id.strip()
        self._timeout_sec = timeout_sec
        self._opener = opener

    @property
    def configured(self) -> bool:
        return self._enabled and bool(self._bot_token and self._chat_id)

    def send_message(self, text: str, *, chat_id: str | None = None) -> TelegramResult:
        target = (chat_id or self._chat_id).strip()
        if not self._enabled:
            return TelegramResult(False, "disabled")
        if not self._bot_token or not target:
            log.warning("Telegram 설정이 없어 보호자 알림을 보내지 않음")
            return TelegramResult(False, "not_configured")
        if not text.strip():
            return TelegramResult(False, "empty_message")

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        body = json.dumps({"chat_id": target, "text": text}, ensure_ascii=False).encode()
        req = request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with self._opener(req, timeout=self._timeout_sec) as response:
                result = json.loads(response.read().decode("utf-8"))
            if not result.get("ok", False):
                log.error("Telegram API가 실패를 반환함: %s", result)
                return TelegramResult(False, "api_error")
            return TelegramResult(True)
        except Exception as exc:
            # 외부 채널 장애가 MQTT 수신을 중단시키면 안 된다.
            log.exception("Telegram 보호자 알림 전송 실패")
            return TelegramResult(False, str(exc))

    def send_fall_escalation(
        self, *, title: str, message: str, notify_id: str, sent_ts: float
    ) -> TelegramResult:
        text = (
            "🚨 HESTIA 긴급 알림\n"
            f"{title}\n"
            f"{message}\n\n"
            "사용자 확인이 없어 보호자에게 전달했습니다.\n"
            f"알림 ID: {notify_id}"
        )
        return self.send_message(text)

    def send_weekly_summary(self, text: str) -> TelegramResult:
        """주간 수면·식사 요약 전달용 진입점."""
        return self.send_message("📊 HESTIA 주간 생활 요약\n" + text)
