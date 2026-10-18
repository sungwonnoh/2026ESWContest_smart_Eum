"""활동 묶음 추적.

activity 는 기억이 없다. 매 순간 센서를 보고 10종 점수를 다시 계산할 뿐,
09:20 의 COOKING 과 09:38 의 EATING 이 같은 식사인지 모른다.

FSM 이 그 '묶음'이라는 개념을 들고 있는 유일한 곳이다.
그리고 묶음의 시작 시각 — t0 — 이 KDE 학습의 입력이다.

명세: t0 는 활동 묶음의 시작 시각. COOKING -> EATING 전이 시에도 유지되어
KDE 에 일관된 값을 제공한다.

방향은 한쪽이다. FSM 은 activity 를 읽기만 하고 t0 를 돌려준다.
FSM 이 점수를 되돌려 바꾸면 순환이 생겨 추적이 불가능해진다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from .clock import Clock
from .config import Config
from .timeutil import day_key
from .world import PowerState, WorldState
from .context import Context

log = logging.getLogger(__name__)


# ==================================================================== t0 로그


@dataclass(frozen=True, slots=True)
class T0Entry:
    """활동 묶음 한 건. KDE 학습의 원재료."""

    date: str              # YYYY-MM-DD (KST)
    type: str              # meal / hydration / sleep_start / sleep_end
    t0: float              # 묶음 시작 시각 (epoch)
    source: str            # sensor / diary / aruba
    prompted: bool         # 시스템 유도로 일어난 행동인가
    duration_sec: float = 0.0

    # sleep_start 전용
    area: str | None = None
    method: str | None = None        # banner / dim / none
    confidence: float | None = None  # 0.9 / 0.9 / 0.6
    awake_areas: tuple[str, ...] | None = None

    # meal 전용
    eat_t0: float | None = None      # 처음 EATING 에 들어간 시각


@runtime_checkable
class T0Log(Protocol):
    def write(self, entry: T0Entry) -> None: ...
    def of_date(self, date: str, *, tail: int = 200) -> tuple[T0Entry, ...]: ...


class MemoryT0Log:
    """테스트와 Replay 용. 파일을 쓰면 결정성이 깨진다."""

    def __init__(self) -> None:
        self.entries: list[T0Entry] = []

    def write(self, entry: T0Entry) -> None:
        self.entries.append(entry)

    def of_type(self, type_: str) -> tuple[T0Entry, ...]:
        return tuple(e for e in self.entries if e.type == type_)

    def of_date(self, date: str, *, tail: int = 200) -> tuple[T0Entry, ...]:
        return tuple(e for e in self.entries if e.date == date)


class FileT0Log:
    """JSONL append. RPi5 는 /data 만 쓸 수 있다.

    on_write 가 있으면 발행까지 간다 — RPi4 가 hestia/# 전량 적재로
    수집한다. 파일은 그래도 남는다: 브로커가 죽어 있는 동안의 기록이
    끊기면 안 되고, 재시작 시 오늘 기록 복원은 of_date() 가 파일을 읽는다.
    """

    def __init__(self, path: Path, on_write: Callable[[dict], None] | None = None) -> None:
        self._path = path
        self._on_write = on_write

    def write(self, entry: T0Entry) -> None:
        payload = asdict(entry)

        # 파일이 먼저다. 발행이 실패해도 기록은 남아야 한다.
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("t0 로그 기록 실패: %s", exc)

        if self._on_write is None:
            return
        try:
            self._on_write(payload)
        except Exception as exc:
            # 발행 실패가 엔진을 멈추게 해서는 안 된다
            log.warning("t0 발행 실패: %s", exc)

    def of_date(self, date: str, *, tail: int = 200) -> tuple[T0Entry, ...]:
        """그 날짜의 기록. 재시작 시 오늘 루틴을 복원하는 데 쓴다.
           파일 끝 tail 줄만 읽는다.
        """
        if not self._path.exists():
            return ()
        try:
            with self._path.open(encoding="utf-8") as fp:
                lines = fp.readlines()[-tail:]
        except OSError as exc:
            log.warning("t0 로그 읽기 실패: %s", exc)
            return ()

        out: list[T0Entry] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("date") != date:
                    continue
                out.append(T0Entry(**obj))
            except (json.JSONDecodeError, TypeError) as exc:
                log.warning("t0 로그 줄 건너뜀: %s", exc)
        return tuple(out)


# ==================================================================== Meal FSM


@dataclass(slots=True)
class MealSession:
    t0: float
    opened_state: str
    last_active_at: float          # 묶음 상태였던 마지막 시각
    prompted: bool = False
    eat_t0: float | None = None    # 처음 EATING 에 들어간 시각


class MealFSM:
    """식사 묶음을 열고 닫으며 t0 를 보관한다.

        IDLE ──COOKING/EATING 진입──→ ACTIVE ──이탈·타임아웃──→ IDLE
                                          ↑
                                     t0 고정
    """

    def __init__(
        self,
        clock: Clock,
        config: Config,
        world: WorldState,
        t0log: T0Log | None = None,
    ) -> None:
        self._clock = clock
        self._config = config
        self._world = world
        self._log = t0log
        self.session: MealSession | None = None
        self._timed_out = False        # 타임아웃 후 재개 차단
        self._closed_at = 0.0          # 직전 묶음이 끝난 시각
        self.on_close: Callable[[MealSession, float], None] | None = None

    @property
    def t0(self) -> float | None:
        return self.session.t0 if self.session else None

    def update(
            self, state: str, in_meal_area: bool = False,
            since: float | None = None,
    ) -> list[tuple[str, float]]:
        """activity 상태를 받아 묶음을 열거나 닫는다. 예약할 타이머를 돌려준다.

        in_meal_area 는 '식사 구역에 아직 있는가'다. 조리가 끝나고 EATING 판정이
        서기 전 구간은 KITCHEN_MISC 인데, 그것으로 묶음을 끊으면
        COOKING -> EATING 사이에서 t0 가 리셋된다.

        since 는 그 상태가 시작된 시각(activity.since)이다. eat_t0 에 쓴다.
        """
        now = self._clock.now()
        timers: list[tuple[str, float]] = []
        open_states = tuple(
            self._config.value("fsm", "meal", "open_states", default=["COOKING", "EATING"])
        )

        if state in open_states:
            if self.session is None:
                if self._timed_out:
                    return timers
                if not self._open(state, now):
                    return timers       # 근거 시각이 없어 열지 못함
            elif now - self.session.t0 >= self._timeout():
                self._close(now, use_now=True)
                self._timed_out = True
                return timers
            else:
                self.session.last_active_at = now

            if state == "EATING" and self.session.eat_t0 is None:
                self.session.eat_t0 = since if since is not None else now
            
            timers.append(("meal-timeout", self.session.t0 + self._timeout()))
            return timers

        self._timed_out = False        # 묶음 상태를 벗어나면 차단 해제

        if self.session is None:
            return timers

        # 세션이 열려 있고 주방에 아직 있으면 묶음은 이어진다.
        # 조리 종료 후 EATING 판정이 서기까지의 공백(KITCHEN_MISC)을 흡수한다.
        if in_meal_area and state == "KITCHEN_MISC":
            self.session.last_active_at = now
            timers.append(("meal-timeout", self.session.t0 + self._timeout()))
            return timers

        grace = float(self._config.value("fsm", "meal", "close_grace_sec", default=600))
        idle = now - self.session.last_active_at

        if now - self.session.t0 >= self._timeout():
            self._close(now, use_now=True)
            self._timed_out = True
        elif idle >= grace and not self._cooking():
            # 묶음은 last_active_at 에 끝났고 우리가 grace 만큼 기다린 것뿐이다
            # 단 조리 중(인덕션 불켜짐)이면 닫지 않는다 (끓이는 동안 자리 비움 상황)
            self._close(now)
        elif self._cooking():
            timers.append(("meal-close", now + grace))
        else:
            timers.append(("meal-close", self.session.last_active_at + grace))

        return timers

    def mark_prompted(self) -> None:
        """시스템 유도로 시작된 묶음임을 표시한다.

        명세: 09:30 에 '물 드세요'를 보내 09:32 에 마셨다면, 그대로 학습하면
        '이 사람은 09:32 에 물을 마신다'가 되지만 실제로는 시스템이 만든 패턴이다.
        알림 층이 생기면 여기를 호출한다.
        """
        if self.session is not None:
            self.session.prompted = True

    # ------------------------------------------------------------ 내부

    def _open(self, state: str, now: float) -> bool:
        #근거 시각을 찾지 못하면 열지 않음
        t0 = self._evidence_t0(state, now)
        if t0 is None:
            log.debug("근거 시각 없음 — 묶음을 열지 않는다 (%s)", state)
            return False
        
        self.session = MealSession(t0=t0, opened_state=state, last_active_at=now)
        log.debug("식사 묶음 시작 t0=%s (%s)", t0, state)
        return True

    def _close(self, now: float, *, use_now: bool = False) -> None:
        s = self.session
        self.session = None
        if s is None:
            return

        # 타임아웃으로 닫을 때는 묶음이 now 까지 이어진 것이다.
        # 이탈로 닫을 때는 last_active_at 에 끝났고 grace 만큼 기다린 것뿐이다.
        duration = (now if use_now else s.last_active_at) - s.t0
        self._closed_at = now if use_now else s.last_active_at

        min_dur = float(self._config.value("fsm", "meal", "min_duration_sec", default=120))
        if duration < min_dur:
            log.debug("식사 묶음 폐기 — 너무 짧음 (%.0f초)", duration)
            return

        if self._log is not None:
            self._log.write(
                T0Entry(
                    date=day_key(s.t0),
                    type="meal",
                    t0=s.t0,
                    source="sensor",
                    prompted=s.prompted,
                    duration_sec=duration,
                    eat_t0=s.eat_t0,
                )
            )
        log.debug(
            "식사 묶음 종료 t0=%s eat_t0=%s duration=%.0f", 
            s.t0, s.eat_t0, duration
        )
        if self.on_close is not None:
            self.on_close(s, now if use_now else s.last_active_at)

    def _evidence_t0(self, state: str, now: float) -> float | None:
        """근거가 생긴 시각을 t0 로 쓴다. 상태가 바뀐 시각이 아니다.(찾지 못하면 None-retained)
        """
        lookback = float(
            self._config.value("fsm", "meal", "cooking_lookback_sec", default=7200)
        )

        starts = [
            st.on_since
            for role in ("MEAL", "MEAL_HEAT")
            for st in self._world.sensors_by_role(role)
            if isinstance(st, PowerState)
            and st.on_since is not None
            and now - st.on_since <= lookback
            and st.on_since > self._closed_at      # 이미 닫힌 묶음의 근거는 남의 것이다
        ]
        if starts:
            return min(starts)

        # 전력 근거가 없으면 주방 체류 시작 시각
        meal_areas = self._config.areas_with_role("MEAL")
        dwells = [self._world.dwell_sec(a) for a in meal_areas]
        best = max(dwells, default=0.0)
        if best > 0:
            return now - best

        return None

    def _timeout(self) -> float:
        return float(self._config.value("fsm", "meal", "session_timeout_sec", default=7200))

    def _cooking(self) -> bool:
        """조리 기구가 켜져 있는가.
            불이 켜져 있는 동안은 그 식사가 조리가 진행 중
        """
        return self._world.any_power_on("MEAL")

# ==================================================================== day FSM

@dataclass(frozen=True, slots=True)
class DayState(Context):
    """명세의 context/day.

    activity 는 '지금'의 상태, day 는 '오늘 하루'의 누적이다.
    시간 스케일이 달라 별도로 유지한다.

    기록이지 추론이 아니므로 confidence·factors 를 쓰지 않는다.
    Context 를 상속하는 것은 발행 경로를 하나로 두기 위해서다.
    """

    date: str = ""
    meals: tuple[float, ...] = ()
    hydrations: tuple[float, ...] = ()
    medications: tuple[float, ...] = ()
    sleep_start: float | None = None
    sleep_end: float | None = None
    sleep_interruptions: int = 0
    bathroom_visits: int = 0
    resleep_minutes: tuple[float, ...] = ()

    def payload(self, now: float) -> dict[str, Any]:
        return {
            "date": self.date,
            "meals": list(self.meals),
            "hydrations": list(self.hydrations),
            "medications": list(self.medications),
            "sleep_start": self.sleep_start,
            "sleep_end": self.sleep_end,
            "sleep_interruptions": self.sleep_interruptions,
            "bathroom_visits": self.bathroom_visits,
            "resleep_minutes": list(self.resleep_minutes),
        }

    def same_as(self, other: Context | None) -> bool:
        if not isinstance(other, DayState):
            return False
        return (
            self.date == other.date
            and self.meals == other.meals
            and self.hydrations == other.hydrations
            and self.medications == other.medications
            and self.sleep_start == other.sleep_start
            and self.sleep_end == other.sleep_end
            and self.sleep_interruptions == other.sleep_interruptions
            and self.bathroom_visits == other.bathroom_visits
            and self.resleep_minutes == other.resleep_minutes
        )


class DayFSM:
    """오늘 무엇을 했는지 기록한다.

    기상 판정을 하지 않는다 — 무엇이 기상인가는 배치가 사후에 정한다.
    날짜가 바뀌면 목록을 비운다.
    """

    def __init__(
        self,
        clock: Clock,
        config: Config,
        world: WorldState,
        t0log: T0Log | None = None,
    ) -> None:
        self._clock = clock
        self._config = config
        self._world = world
        self._log = t0log
        self._sleep_start: float | None = None
        self._last_wake_at: float | None = None
        self._sleep_interruptions = 0
        self._bathroom_visits = 0
        self._resleep_minutes: list[float] = []
        now = clock.now()
        self.state = DayState(name="day", since=now, date=day_key(now))

    def tick(self) -> list[tuple[str, float]]:
        """날짜 전환만 본다. 타이머는 없다."""
        self._roll_day(self._clock.now())
        return []

    def restore(self) -> None:
        """기동 시 오늘 기록을 되살린다. 날짜 기준이다."""
        if self._log is None:
            return
        today = day_key(self._clock.now())

        buckets: dict[str, list[float]] = {
            "meals": [], "hydrations": [], "medications": [],
        }
        for e in self._log.of_date(today):
            key = f"{e.type}s"
            if key not in buckets:
                continue
            # meals 는 eat_t0 기준이다. 조리만 하고 안 먹은 묶음은
            # eat_t0 가 없으니 끼니로 세지 않는다.
            ts = e.eat_t0 if e.type == "meal" else e.t0
            if ts is not None:
                buckets[key].append(ts)

        self.state = DayState(
            name="day", since=self._clock.now(), date=today,
            meals=tuple(sorted(buckets["meals"])),
            hydrations=tuple(sorted(buckets["hydrations"])),
            medications=tuple(sorted(buckets["medications"])),
        )
        log.info(
            "오늘 기록 복원: meals=%d hydrations=%d medications=%d",
            len(self.state.meals), len(self.state.hydrations),
            len(self.state.medications),
        )

    def note_hydration(self, prompted: bool = False) -> None:
        """정수기 급수."""
        self._append("hydrations", "hydration", prompted)

    def note_meal(self, session: Any, closed_at: float) -> None:
        """MealFSM 이 묶음을 닫을 때 호출. t0 로그는 MealFSM 이 발행.
           먹은 시각(eat_t0) 기준으로.
        """
        if session.eat_t0 is None:
            return

        self._roll_day(self._clock.now())
        if day_key(session.eat_t0) != self.state.date:
            return                       # 자정을 넘긴 묶음 — 어제 것이다
        self.state = _replace(
            self.state, meals=(*self.state.meals, session.eat_t0)
        )

    def note_medication(self, prompted: bool = False) -> None:
        self._append("medications", "medication", prompted)

    def note_sleep_start(
        self, t0: float, awake_areas: tuple[str, ...] | None = None
    ) -> None:
        """수면 확정 시 기록한다. 재수면은 야간 각성으로 집계한다."""
        self._roll_day(t0)
        is_new_night = (
            self._sleep_start is None
            or self._last_wake_at is None
            or t0 - self._last_wake_at > 3 * 3600
        )
        if is_new_night:
            self._sleep_start = t0
            self._last_wake_at = None
            self._sleep_interruptions = 0
            self._bathroom_visits = 0
            self._resleep_minutes = []
        else:
            self._sleep_interruptions += 1
            if awake_areas and "bathroom" in awake_areas:
                self._bathroom_visits += 1
            self._resleep_minutes.append(max(0.0, (t0 - self._last_wake_at) / 60.0))
        self.state = _replace(
            self.state,
            sleep_start=self._sleep_start,
            sleep_interruptions=self._sleep_interruptions,
            bathroom_visits=self._bathroom_visits,
            resleep_minutes=tuple(self._resleep_minutes),
        )

    def note_sleep_end(self, t0: float) -> None:
        """수면 종료 시 현재 날짜의 일별 수면 요약을 갱신한다."""
        if self._sleep_start is None:
            return
        self._roll_day(t0)
        self._last_wake_at = t0
        self.state = _replace(
            self.state,
            sleep_start=self._sleep_start,
            sleep_end=t0,
            sleep_interruptions=self._sleep_interruptions,
            bathroom_visits=self._bathroom_visits,
            resleep_minutes=tuple(self._resleep_minutes),
        )

    def done_at(self, kind: str) -> float | None:
        """그 행동을 마지막으로 한 시각. 알림 층의 comply 판정에 쓴다."""
        times = getattr(self.state, f"{kind}s", ())
        return times[-1] if times else None

    # ------------------------------------------------------------ 내부

    def _roll_day(self, now: float) -> None:
        """날짜가 바뀌면 오늘 기록을 비운다.

        기상 확정에 묶으면 '무엇이 기상인가' 를 실시간으로 판정해야
        하는데, 그 판단은 배치가 사후에 하기로 했다.
        """
        today = day_key(now)
        if self.state.date != today:
            self.state = DayState(name="day", since=now, date=today)
            log.debug("날짜 전환 — 오늘 기록 초기화 %s", today)

    def _append(self, field: str, type_: str, prompted: bool) -> None:
        now = self._clock.now()
        self._roll_day(now)
        self.state = _replace(
            self.state, **{field: (*getattr(self.state, field), now)}
        )
        self._log_entry(type_, prompted)

    def _log_entry(self, type_: str, prompted: bool) -> None:
        if self._log is None:
            return
        now = self._clock.now()
        self._log.write(
            T0Entry(
                date=day_key(now), type=type_, t0=now,
                source="sensor", prompted=prompted, duration_sec=0.0,
            )
        )


def _replace(state: DayState, **changes: Any) -> DayState:
    from dataclasses import replace

    return replace(state, **changes)
