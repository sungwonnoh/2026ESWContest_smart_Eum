"""최신 MQTT 상태 cache 와 Context → API 변환.

- 원본 MQTT payload 는 그대로 보존한다 (Monitor 와 디버깅용).
- API 응답은 Envelope 를 뺀 Domain 표현으로 만든다.
- 판단 근거(factors)를 사람이 읽는 label 로 바꾸는 것만 한다. 새로 판단하지 않는다.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Callable

from ..repositories.history_repository import ContextRecord, HistoryRepository
from ..schemas.common import MqttEnvelope
from ..schemas.context import CONTEXT_NAMES, to_domain
from ..schemas.model import Explanation, ExplanationFactor, RegistryEntry

AREA_NAMES = {
    "living": "거실",
    "kitchen": "주방",
    "bedroom": "침실",
    "bathroom": "욕실",
    "utility": "다용도실",
    "entrance": "현관",
}

FACTOR_NAMES = {
    "presence": "재실",
    "area": "위치",
}

# 켜짐/꺼짐으로 읽는 근거: (True 일 때, False 일 때). None 이면 그쪽 값은 보이지 않는다.
BOOL_FACTORS = {
    "cooking_on": ("조리 기구 사용 중", "조리 기구 꺼짐"),
    "fridge_recent": ("최근 냉장고 사용", "최근 냉장고 사용 없음"),
    "eating_sustain": ("식사가 이어지는 중", "식사 신호 없음"),
}

# 있으면 판단을 믿기 어렵다는 뜻이라 충족으로 표시하지 않는다.
WARNING_FACTORS = {
    "sensor_fault": "센서 이상 감지",
    "below_min_score": "판단할 근거가 부족함",
}

# 경과 시간(초) 근거. {} 에 "7분" 처럼 들어간다.
DURATION_FACTORS = {
    "bed_occupied_sec": "침대에 {} 머묾",
    "bedroom_still_sec": "침실에서 {} 움직임 없음",
    "sofa_occupied_sec": "소파에 {} 머묾",
    "living_still_sec": "거실에서 {} 움직임 없음",
    "meal_area_dwell_sec": "식사 공간에 {} 머묾",
    "cooking_off_sec": "조리 기구 끈 지 {}",
    "heated_sec": "데우기 끝난 지 {}",
    "tv_remote_sec": "TV 리모컨 사용 후 {}",
    "all_areas_quiet_sec": "모든 공간에 {} 반응 없음",
    "door_exit_sec_ago": "현관문으로 나간 지 {}",
    "concurrent_sec": "여러 공간에서 {} 동시 감지",
}

# 센서별 근거는 "{vid}_{종류}" 로 온다. 신호 세기 등 여기 없는 종류는 보이지 않는다.
SENSOR_SUFFIXES = ("_present", "_occupied", "_last_motion_sec", "_static_denied")


def iso(ts: float | None) -> str | None:
    """epoch 초 → 로컬 타임존 ISO 8601 (예: 2026-09-30T19:30:00+09:00)."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def format_duration(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return f"{total}초"
    if total < 3600:
        return f"{total // 60}분"
    hours, rest = divmod(total // 60, 60)
    return f"{hours}시간 {rest}분" if rest else f"{hours}시간"


def _sensor_label(key: str, value: Any, area_of: Callable[[str], str | None]) -> str | None:
    for suffix in SENSOR_SUFFIXES:
        if not key.endswith(suffix):
            continue
        area = area_of(key[: -len(suffix)])
        place = f"{AREA_NAMES.get(area, area)} " if area else ""
        if suffix == "_present":
            return f"{place}재실 센서: {'사람 감지' if value else '사람 없음'}"
        if suffix == "_occupied":
            return f"{place}압력 패드: {'사용 중' if value else '비어 있음'}"
        if suffix == "_last_motion_sec":
            return f"{place}마지막 움직임: {format_duration(value)} 전"
        return f"{place}정지 신호는 사람이 아닌 것으로 판단"
    return None


def factor_label(
    key: str, value: Any, area_of: Callable[[str], str | None] = lambda _: None
) -> str | None:
    """사람이 읽는 문장. 사용자에게 보일 근거가 아니면 None."""
    if value is None or value == []:
        return None
    if key == "area" and isinstance(value, str):
        return f"{AREA_NAMES.get(value, value)} 재실 감지"
    if key in FACTOR_NAMES and isinstance(value, bool):
        return f"{FACTOR_NAMES[key]} 감지" if value else f"{FACTOR_NAMES[key]} 없음"
    if key in BOOL_FACTORS:
        return BOOL_FACTORS[key][0 if value else 1]
    if key in WARNING_FACTORS:
        return WARNING_FACTORS[key] if value else None
    if key in DURATION_FACTORS:
        return DURATION_FACTORS[key].format(format_duration(value))
    if key == "hour":
        return f"시간대: {value}시"
    if key == "tv_power":
        return "TV 켜짐" if value == "ON" else "TV 꺼짐"
    if key == "concurrent_areas":
        return f"사람이 감지된 공간: {value}곳"
    if key == "strong_areas":
        return f"신호가 뚜렷한 공간: {value}곳"
    if key == "asleep_area":
        return f"{AREA_NAMES.get(value, value)}에서 수면 중"
    if key == "offline_sensors":
        return f"연결이 끊긴 센서 {len(value)}개"
    if key == "offline_nodes":
        return f"연결이 끊긴 장치 {len(value)}개"
    return _sensor_label(key, value, area_of)


def to_factors(
    factors: dict[str, Any], area_of: Callable[[str], str | None] = lambda _: None
) -> list[ExplanationFactor]:
    """보여줄 근거만 고른다. 충족 표시는 참·거짓으로 갈리는 근거에만 의미가 있다."""
    out: list[ExplanationFactor] = []
    for key, value in factors.items():
        label = factor_label(key, value, area_of)
        if label is None:
            continue
        satisfied = value if isinstance(value, bool) else True
        if key in WARNING_FACTORS or key.endswith("_static_denied"):
            satisfied = False
        out.append(ExplanationFactor(key=key, value=value, label=label, satisfied=satisfied))
    return out


class StateCache:
    """MQTT 로 받은 최신 상태. 여러 스레드에서 읽고 쓴다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._contexts: dict[str, MqttEnvelope] = {}
        self._context_raw: dict[str, dict[str, Any]] = {}
        self._devices: dict[str, dict[str, Any]] = {}
        self._device_seen: dict[str, float] = {}
        self._device_events: dict[str, dict[str, Any]] = {}
        self._sensors: dict[str, dict[str, Any]] = {}
        self._models: dict[str, dict[str, Any]] = {}
        self._registry: dict[str, RegistryEntry] = {}
        self.profile: str | None = None

    # ------------------------------------------------------------ writes

    def set_context(self, name: str, model: MqttEnvelope, raw: dict[str, Any]) -> None:
        with self._lock:
            self._contexts[name] = model
            self._context_raw[name] = raw

    def set_device(self, vid: str, raw: dict[str, Any], recv_ts: float) -> None:
        with self._lock:
            self._devices[vid] = raw
            self._device_seen[vid] = recv_ts

    def device(self, vid: str) -> tuple[dict[str, Any], float] | None:
        """마지막 상태 payload 와 수신 시각."""
        with self._lock:
            if vid not in self._devices:
                return None
            return self._devices[vid], self._device_seen[vid]

    def set_device_event(self, vid: str, raw: dict[str, Any]) -> None:
        with self._lock:
            self._device_events[vid] = raw

    def set_sensor(self, vid: str, raw: dict[str, Any]) -> None:
        with self._lock:
            self._sensors[vid] = raw

    def set_model(self, name: str, raw: dict[str, Any]) -> None:
        with self._lock:
            self._models[name] = raw

    def set_registry(self, entries: list[RegistryEntry]) -> None:
        with self._lock:
            self._registry = {e.virtual_id: e for e in entries}

    # ------------------------------------------------------------ reads

    def registry_area(self, vid: str) -> str | None:
        """registry 에 등록된 장치의 공간(area). 모르면 None."""
        with self._lock:
            entry = self._registry.get(vid)
            return entry.area if entry else None

    def current_context(self) -> dict[str, dict[str, Any]]:
        """GET /api/v1/context/current — 받은 context 만, 명세 순서대로."""
        with self._lock:
            return {
                name: to_domain(self._contexts[name])
                for name in CONTEXT_NAMES
                if name in self._contexts
            }

    def known_devices(self) -> dict[str, dict[str, Any]]:
        """virtual_id → {device_type, area}. registry 와 실제 수신 기록을 합친다."""
        with self._lock:
            known: dict[str, dict[str, Any]] = {}
            for vid, entry in self._registry.items():
                if entry.device_type and entry.enabled:
                    known[vid] = {"device_type": entry.device_type, "area": entry.area}
            for vid, raw in self._devices.items():
                known.setdefault(vid, {"device_type": raw.get("device_type"), "area": None})
            return known

    def snapshot(self) -> dict[str, Any]:
        """Monitor 접속 직후 한 번 보내는 전체 상태 (원본 payload)."""
        with self._lock:
            return {
                "context": dict(self._context_raw),
                "device_state": dict(self._devices),
                "device_event": dict(self._device_events),
                "sensor_state": dict(self._sensors),
                "model": {
                    name: {"trained_at": raw.get("trained_at"), "sent_ts": raw.get("sent_ts")}
                    for name, raw in self._models.items()
                },
                "profile": self.profile,
            }


class ExplanationService:
    def __init__(
        self, history: HistoryRepository, area_of: Callable[[str], str | None] = lambda _: None
    ) -> None:
        self._history = history
        self._area_of = area_of

    def latest(self) -> Explanation | None:
        rows = self._history.list_explainable(limit=1)
        return self._build(rows[0]) if rows else None

    def list(self, limit: int = 20) -> list[Explanation]:
        return [self._build(r) for r in self._history.list_explainable(limit=limit)]

    def get(self, explanation_id: str) -> Explanation | None:
        if not explanation_id.isdigit():
            return None
        row = self._history.get_context(int(explanation_id))
        return self._build(row) if row else None

    def _build(self, row: ContextRecord) -> Explanation:
        notification = self._history.latest_notification_for(str(row.id))
        action = f"'{notification.title}' 알림을 보냈습니다." if notification else None
        return Explanation(
            id=str(row.id),
            context_name=row.context_name,
            state=row.state or "UNKNOWN",
            confidence=row.confidence,
            factors=to_factors(row.factors, self._area_of),
            action=action,
            created_at=iso(row.updated_at),
        )
