"""조회 시점, 캐시, payload.

    push    새 정시 실황이 나오거나 특보가 바뀌면 바로 발행 (reason="update")
    get     요청이 오면 캐시로 바로 응답 (reason="reply")

실황: 관측이 매시 정시 한 번이라 더 자주 조회해도 값은 같다.
      정시 + settle_min 에 조회하고, 이번 정시 자료가 아직이면 retry_sec 마다 다시 본다.
특보: 발표는 아무 때나 나오므로 warn_poll_sec 마다 본다.

시연 모드: 켜져 있는 동안 발행 직전에 폭염주의보와 여름 기온·습도를 덧씌우고 demo=true 를 붙인다.
          조회는 그대로 하므로 관측 시각·강수·풍속·다른 특보는 실제 값이다. demo_ttl_sec 뒤 저절로 꺼진다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .kma import KmaClient, KmaError, Observation, Warnings, base_hour

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
HOUR_SEC = 3600

DEMO_HEAT = {"name": "폭염주의보", "type": "HEAT", "level": "ADVISORY"}
DEMO_TEMPERATURE_C = 33.0
DEMO_HUMIDITY_PCT = 60.0
DEMO_TTL_SEC = 3600


@dataclass(frozen=True)
class Location:
    name: str
    nx: int
    ny: int
    warning_areas: tuple[str, ...] = field(default=())    # 특보 구역 매칭 이름 (예: 서울)

    def to_payload(self) -> dict[str, Any]:
        return {"name": self.name, "nx": self.nx, "ny": self.ny}


class WeatherService:
    def __init__(
        self,
        client: KmaClient,
        location: Location,
        *,
        src_id: str = "rpi4",
        settle_min: float = 12,
        retry_sec: float = 120,
        stale_sec: float = 5400,
        warn_poll_sec: float = 600,
    ) -> None:
        self._client = client
        self._location = location
        self._src_id = src_id
        self._settle_sec = settle_min * 60
        self._retry_sec = retry_sec
        self._stale_sec = stale_sec
        self._warn_poll_sec = warn_poll_sec
        self._warn_enabled = bool(location.warning_areas)

        self.cache: Observation | None = None
        self.warnings: Warnings | None = None   # None = 아직 모름 (조회 전·실패)
        self.next_poll: float = 0.0             # 0 → 기동 직후 바로 조회
        self.next_warn_poll: float = 0.0
        self.demo_until: float = 0.0            # 0 → 시연 모드 꺼짐

    # ------------------------------------------------------------ push

    def due(self, now: float) -> bool:
        return now >= self.next_poll or (self._warn_enabled and now >= self.next_warn_poll)

    def poll(self, now: float) -> bool:
        """때가 된 것만 조회한다. 발행할 새 내용이 생겼으면 True.
        실황이 아직 한 번도 없으면 특보만 바뀌어도 발행하지 않는다 (실황과 함께 나간다)."""

        changed = False
        if now >= self.next_poll:
            changed |= self._poll_observation(now)
        if self._warn_enabled and now >= self.next_warn_poll:
            changed |= self._poll_warnings(now)
        return changed and self.cache is not None

    def _poll_observation(self, now: float) -> bool:
        try:
            obs = self._client.latest(now)
        except KmaError as exc:
            log.warning("실황 조회 실패 — 마지막 값 유지: %s", exc)
            self.next_poll = now + self._retry_sec
            return False

        self.next_poll = self._next_poll(now, obs)
        if obs == self.cache:
            return False
        self.cache = obs
        log.info("새 실황: %s", obs)
        return True

    def _next_poll(self, now: float, obs: Observation) -> float:
        hour = base_hour(now).timestamp()
        if obs.observed_at >= hour:
            # 이번 정시 자료를 받았다 → 다음 정시 + settle
            return hour + HOUR_SEC + self._settle_sec
        # 아직 한 시간 전 자료 → 이번 정시 자료가 나올 때까지 재시도
        return max(now + self._retry_sec, hour + self._settle_sec)

    def _poll_warnings(self, now: float) -> bool:
        try:
            w = self._client.warnings(now, self._location.warning_areas)
        except KmaError as exc:
            # 실패해도 마지막 특보를 유지한다. 한 번도 못 받았으면 null(모름)로 나간다
            log.warning("특보 조회 실패 — 마지막 값 유지: %s", exc)
            self.next_warn_poll = now + self._retry_sec
            return False

        self.next_warn_poll = now + self._warn_poll_sec
        if self.warnings is not None and w.items == self.warnings.items:
            self.warnings = w                   # 근거 통보문 시각만 갱신, 발행할 변화는 아님
            return False
        self.warnings = w
        log.info("특보 변경: %s", [x.name for x in w.items] or "없음")
        return True

    # ------------------------------------------------------------ get

    def reply(self, now: float) -> bool:
        """요청 응답용. 실황이 없거나 오래됐으면, 특보를 아직 모르면 한 번 조회해 본다.
        응답할 실황이 있으면 True."""

        if self.is_stale(now):
            self._poll_observation(now)
        if self._warn_enabled and self.warnings is None:
            self._poll_warnings(now)
        return self.cache is not None

    def is_stale(self, now: float) -> bool:
        return self.cache is None or now - self.cache.observed_at > self._stale_sec

    # ------------------------------------------------------------ 시연

    def set_demo(self, on: bool, now: float, ttl_sec: float = DEMO_TTL_SEC) -> bool:
        """시연 모드를 켜거나 끈다. 발행할 내용이 바뀌었으면 True."""
        was = self.demo_active(now)
        self.demo_until = now + ttl_sec if on else 0.0
        log.info("시연 모드 %s", f"켜짐 ({ttl_sec:.0f}초)" if on else "꺼짐")
        return (on or was) and self.cache is not None

    def demo_active(self, now: float) -> bool:
        return now < self.demo_until

    def demo_expired(self, now: float) -> bool:
        """시연 시간이 방금 지났으면 한 번만 True — 실제 값을 다시 발행할 때."""
        if self.demo_until and now >= self.demo_until:
            self.demo_until = 0.0
            log.info("시연 모드 시간 만료 — 실제 값으로 돌아감")
            return self.cache is not None
        return False

    # ------------------------------------------------------------ payload

    def payload(self, *, reason: str, now: float) -> dict[str, Any]:
        if self.cache is None:
            raise ValueError("실황이 없어 payload 를 만들 수 없음")
        out = self._real_payload(reason=reason, now=now)
        if self.demo_active(now):
            warnings = [w for w in out["warnings"] or [] if w["type"] != "HEAT"]
            out.update(
                temperature_c=DEMO_TEMPERATURE_C,
                humidity_pct=DEMO_HUMIDITY_PCT,
                warnings=[*warnings, dict(DEMO_HEAT)],
                warnings_issued_at=out["warnings_issued_at"] or int(now),
                demo=True,
            )
        return out

    def _real_payload(self, *, reason: str, now: float) -> dict[str, Any]:
        w = self.warnings
        return {
            "version": SCHEMA_VERSION,
            "sent_ts": int(now),
            "src_id": self._src_id,
            "reason": reason,
            "source": "kma",
            "location": self._location.to_payload(),
            **self.cache.to_payload(),
            # null = 모름(조회 전·실패·구역 미설정), [] = 집 구역에 특보 없음
            "warnings": None if w is None else [x.to_payload() for x in w.items],
            "warnings_issued_at": None if w is None else w.issued_at,
        }
