"""보호자에게 전달할 일별 활동 요약."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

from ..repositories.history_repository import HistoryRepository
from .telegram_service import TelegramResult, TelegramService


class CaregiverSummaryService:
    def __init__(
        self,
        history: HistoryRepository,
        telegram: TelegramService,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._history = history
        self._telegram = telegram
        self._today = today

    def build(self, days: int = 7) -> str:
        days = max(1, min(days, 31))
        end = self._today()
        start = end - timedelta(days=days - 1)
        records = self._history.list_days(start.isoformat())
        by_date = {record.date: record for record in records}
        meal_counts = [len(by_date[d.isoformat()].meals) for d in
                       (start + timedelta(days=i) for i in range(days))
                       if d.isoformat() in by_date]
        meal_total = sum(meal_counts)

        lines = [f"기간: {start.isoformat()} ~ {end.isoformat()}"]
        if not meal_counts:
            lines.append("식사 기록: 아직 수집된 기록이 없습니다.")
        else:
            distribution = ", ".join(
                f"{count}끼 {meal_counts.count(count)}일"
                for count in sorted(set(meal_counts))
            )
            lines.extend([
                f"식사: {meal_total}회 (기록된 {len(meal_counts)}일 기준 하루 평균 "
                f"{meal_total / len(meal_counts):.1f}회)",
                f"식사 횟수 분포: {distribution}",
            ])

        sleep_rows = [
            by_date[d.isoformat()].payload
            for d in (start + timedelta(days=i) for i in range(days))
            if d.isoformat() in by_date and _has_sleep_fields(by_date[d.isoformat()].payload)
        ]
        if sleep_rows:
            interruptions = [
                float(row.get("sleep_interruptions", row.get("night_awakenings", 0)))
                for row in sleep_rows
            ]
            bathroom = [
                float(row.get("bathroom_visits", row.get("night_bathroom_visits", 0)))
                for row in sleep_rows
            ]
            lines.append(
                f"수면: {len(sleep_rows)}박 기록, 평균 야간 각성 {sum(interruptions) / len(interruptions):.1f}회, "
                f"평균 야간 화장실 {sum(bathroom) / len(bathroom):.1f}회"
            )
        else:
            lines.append("수면: 수면 요약 원천 데이터가 아직 수집되지 않았습니다.")
        return "\n".join(lines)

    def send(self, days: int = 7) -> TelegramResult:
        return self._telegram.send_weekly_summary(self.build(days))


def _has_sleep_fields(payload: dict) -> bool:
    return any(
        key in payload
        for key in (
            "sleep_interruptions", "night_awakenings", "bathroom_visits",
            "night_bathroom_visits", "sleep_duration_min",
        )
    )
