"""Telegram 보호자 명령과 주간 자동 발송."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable

from .caregiver_summary_service import CaregiverSummaryService
from .telegram_service import TelegramService

log = logging.getLogger(__name__)


class CaregiverBotService:
    def __init__(
        self,
        telegram: TelegramService,
        summary: CaregiverSummaryService,
        weekly_enabled: bool = False,
        weekly_weekday: int = 6,
        weekly_hour: int = 18,
        weekly_minute: int = 0,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._telegram = telegram
        self._summary = summary
        self._weekly_enabled = weekly_enabled
        self._weekly_weekday = weekly_weekday
        self._weekly_hour = weekly_hour
        self._weekly_minute = weekly_minute
        self._now = now
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset: int | None = None
        self._last_weekly_date: str | None = None

    @property
    def enabled(self) -> bool:
        return self._telegram.configured

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="hestia-telegram-bot", daemon=True
        )
        self._thread.start()
        log.info("Telegram 보호자 봇 시작")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def handle_update(self, update: dict) -> bool:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if str(chat.get("id", "")) != self._telegram.chat_id:
            return False
        text = (message.get("text") or "").strip()
        if not text:
            return False
        parts = text.split(maxsplit=1)
        command = parts[0].split("@", 1)[0].lower()
        argument = parts[1].strip() if len(parts) == 2 else ""
        if command not in {"/today", "/week", "/summary"}:
            self._telegram.send_message("사용법: /today, /week, /summary [1~31일]")
            return True
        days = 1 if command == "/today" else 7
        if command == "/summary" and argument:
            try:
                days = max(1, min(int(argument), 31))
            except ValueError:
                self._telegram.send_message(
                    "기간은 1~31 사이의 숫자로 입력해주세요. 예: /summary 3"
                )
                return True
        self._summary.send(days)
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            self._send_weekly_if_due()
            updates = self._telegram.get_updates(self._offset)
            for update in updates:
                self._offset = int(update.get("update_id", 0)) + 1
                try:
                    self.handle_update(update)
                except Exception:
                    log.exception("Telegram 명령 처리 실패")

    def _send_weekly_if_due(self) -> None:
        now = self._now()
        today = now.date().isoformat()
        if not self._weekly_enabled or self._last_weekly_date == today:
            return
        if (
            now.weekday() == self._weekly_weekday
            and now.hour == self._weekly_hour
            and now.minute == self._weekly_minute
        ):
            self._summary.send(7)
            self._last_weekly_date = today
