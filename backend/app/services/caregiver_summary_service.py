"""보호자에게 전달할 일별 활동 요약."""

from __future__ import annotations

from typing import Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

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

        # 완료된 SLEEPING 구간을 기상일 기준으로 조회한다.
        tz = ZoneInfo("Asia/Seoul")
        since_ts = datetime.combine(
            start, time.min, tzinfo=tz
        ).timestamp()
        until_ts = datetime.combine(
            end + timedelta(days=1), time.min, tzinfo=tz
        ).timestamp()

        sleep_records = self._history.list_completed_sleep(
            since_ts, until_ts
        )

        night_sleep_minutes = 0
        nap_minutes = 0
        night_count = 0
        nap_count = 0

        for record in sleep_records:
            if record.ended_at is None:
                continue

            duration_min = (
                record.ended_at - record.started_at
            ) / 60

            started = datetime.fromtimestamp(
                record.started_at, tz
            )

            # 야간 수면 시간대: 20:00 ~ 다음 날 09:00
            is_night = (
                started.hour >= 20
                or started.hour < 9
            )

            if is_night:
                night_sleep_minutes += duration_min
                night_count += 1
            else:
                nap_minutes += duration_min
                nap_count += 1

        if sleep_records:
            lines.append(
                f"수면: 야간 수면 {night_count}회 "
                f"(총 {night_sleep_minutes / 60:.1f}시간), "
                f"낮잠 {nap_count}회 "
                f"(총 {nap_minutes:.0f}분)"
            )
        else:
            lines.append("수면: 수면 요약 원천 데이터가 아직 수집되지 않았습니다.")

        # 기존 daily_activity 기반 야간 각성·화장실 횟수 유지
        sleep_rows = [
            by_date[d.isoformat()].payload
            for d in (start + timedelta(days=i) for i in range(days))
            if d.isoformat() in by_date
            and _has_sleep_fields(by_date[d.isoformat()].payload)
        ]

        if sleep_rows:
            interruptions = [
                float(
                    row.get(
                        "sleep_interruptions",
                        row.get("night_awakenings", 0),
                    )
                )
                for row in sleep_rows
            ]
            bathroom = [
                float(
                    row.get(
                        "bathroom_visits",
                        row.get("night_bathroom_visits", 0),
                    )
                )
                for row in sleep_rows
            ]

            lines.append(
                f"수면 상세: 평균 야간 각성 "
                f"{sum(interruptions) / len(interruptions):.1f}회, "
                f"평균 야간 화장실 "
                f"{sum(bathroom) / len(bathroom):.1f}회"
            )
        return "\n".join(lines)

    def send(self, days: int = 7) -> TelegramResult:
        return self._telegram.send_weekly_summary(self.build(days))


def _has_sleep_fields(payload: dict) -> bool:
    # context/day may contain zero-valued sleep fields before the first
    # completed night. That is not the same as a collected sleep record.
    return (
        payload.get("sleep_start") is not None
        or payload.get("sleep_end") is not None
        or float(payload.get("sleep_interruptions", payload.get("night_awakenings", 0))) > 0
        or float(payload.get("bathroom_visits", payload.get("night_bathroom_visits", 0))) > 0
        or bool(payload.get("resleep_minutes"))
        or "sleep_duration_min" in payload
    )
