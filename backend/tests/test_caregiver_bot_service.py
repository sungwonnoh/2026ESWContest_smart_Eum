from datetime import datetime

from app.services.caregiver_bot_service import CaregiverBotService
from app.services.telegram_service import TelegramResult, TelegramService


class Summary:
    def __init__(self):
        self.sent = []

    def send(self, days):
        self.sent.append(days)
        return TelegramResult(True)


def make_bot(summary, **kwargs):
    telegram = TelegramService(True, "token", "123", opener=lambda *args, **kwargs: None)
    return CaregiverBotService(telegram, summary, **kwargs)


def update(chat_id, text):
    return {"update_id": 10, "message": {"chat": {"id": chat_id}, "text": text}}


def test_commands_request_expected_summary_periods():
    summary = Summary()
    bot = make_bot(summary)

    assert bot.handle_update(update(123, "/today"))
    assert bot.handle_update(update(123, "/week"))
    assert bot.handle_update(update(123, "/summary 3"))

    assert summary.sent == [1, 7, 3]


def test_other_chat_is_ignored():
    summary = Summary()
    bot = make_bot(summary)

    assert not bot.handle_update(update(999, "/week"))
    assert summary.sent == []


def test_weekly_summary_is_sent_once_at_configured_time():
    summary = Summary()
    bot = make_bot(
        summary,
        weekly_enabled=True,
        weekly_weekday=6,
        weekly_hour=18,
        weekly_minute=0,
        now=lambda: datetime(2026, 10, 18, 18, 0),
    )

    bot._send_weekly_if_due()
    bot._send_weekly_if_due()

    assert summary.sent == [7]
