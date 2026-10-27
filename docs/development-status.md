# Development Status

## Context Engine

- [x] Clock 주입 구조
- [x] MQTT / Replay ingest 단일 진입점
- [x] World State 기본 구조
- [x] Policy 계층 기본 구조
- [x] KDE Model Parser
- [x] KDE ModelStore
- [x] `hestia/model/kde` ingest
- [x] Meal KDE Context 조회
- [x] Tail Probability 기반 Meal Policy
- [x] Predictability 기반 Pattern Reliability
- [x] Replay A/B 개인화 검증
- [x] Context Engine 자동 테스트
- [x] 실제 RPi MQTT E2E 테스트
- [x] retained model 재수신 검증
- [x] HOME / AWAY 상태 검출 (`hestia/context/away`, LWT 기반 `SENSOR_FAULT` 포함)
- [x] meal_done 상태 연결
- [x] FSM / World State와 Meal Policy 통합 (`scenarios.py`)
- [x] 끼니 구간(peaks) 기반 미식사 알림 (`MEAL_PROMPT`)
- [x] 알림 전달·응답 추적·에스컬레이션 (`notify.py`, 2단계 보호자 통보)
- [x] 수분·복약·수면·안전 시나리오
- [ ] Node Health Monitoring

## Learning Engine

- [x] CASAS Aruba 데이터 확보
- [x] `Meal_Preparation begin` 파싱
- [x] Breakfast Proxy 생성
- [x] Context Engine t0 JSONL 수신·저장
- [x] 실제 t0 로그 기반 meal KDE 학습
- [x] `sleep_start` / `sleep_end` 기반 밤잠·기상 표본 추출
- [x] 하루 1회 batch 학습 및 retained MQTT 발행
- [x] Gaussian KDE Baseline
- [x] 24시간 / 15분 / 96-bin density 생성
- [x] Tail Probability
- [x] Entropy Predictability
- [x] KDE MQTT Model Payload 생성
- [x] MQTT Model Publisher 구현
- [x] 식사 peak·끼니별 predictability 계산
- [x] recent weighting / prompted 감쇠 / cold-start blending (옵션, 기본 꺼짐)
- [x] Replay용 A/B KDE 모델 생성
- [x] 실제 RPi4 → MQTT → RPi5 Publish 및 retained 재수신 E2E 검증
- [x] 전체 `meal_time` multi-modal peak 계산
- [x] 주기적 Batch Learning 구현
- [x] 합성 사용자 로그(p1 / p2, 91일) 생성
- [ ] Predictability Threshold 재검증
- [ ] 사용자별 Baseline 영속 저장
- [ ] HMM 기반 모호한 Activity 추론
- [ ] 실제 사용자 로그 60~90일 품질·시간순 holdout 검증

## Current Meal KDE Flow

```text
RPi5 Context Engine (식사 묶음 종료)
  ↓
hestia/log/t0
  ↓
RPi4 hestia-t0-logger → t0_log.jsonl
  ↓
hestia-learning.timer (매일 03:00) → run_learning.py
  ↓
Gaussian KDE → 24h / 15min / 96-bin density
  ↓
끼니 구간(peaks) + Predictability
  ↓
hestia/model/kde (QoS 1, retained)
  ↓
RPi5 ModelStore
  ↓
끼니 구간 안 Tail Probability
  ↓
Meal Policy → MEAL_PROMPT
```

개발·검증에는 CASAS 데이터(Aruba 등)와 합성 로그(`data/synthetic/`)를 같은 경로로 학습한다.

## Replay Validation

동일한 시각 `09:40`에서 사용자별 KDE 분포만 다르게 설정해
개인화 판단이 달라지는 것을 검증했습니다.

```text
User A
tail_probability = 0.0
predictability = 0.35
candidate = True

User B
tail_probability = 0.60
predictability = 0.25
candidate = False
```

즉:

```text
same time
same policy
different KDE
→ different intervention candidate
```

를 확인했습니다.

## Test Status

테스트는 서비스별 `tests/` 폴더에 있습니다.

```bash
cd services/context-engine && python3 -m pytest tests -q
cd services/learning-engine && python3 -m pytest tests -q
cd backend && python3 -m pytest -q
```

| 모듈 | 테스트 파일 | 테스트 함수 |
|---|---:|---:|
| Context Engine | 21 | 672 |
| Learning Engine | 16 | 266 |
| Backend (FastAPI) | 12 | 98 |
| Flutter 앱 | 7 | 73 |
| Weather | 4 | 45 |
| Tasmota bridge | 3 | 39 |

테스트 함수 수 기준이다. `parametrize` 로 늘어나는 케이스는 포함하지 않았다.

## Next Priorities

1. 실제 사용자 로그의 confidence·결측·오탐 품질 지표 수집
2. 최소 60~90일 데이터의 시간순 holdout 검증
3. 평일/주말·재실/외출·끼니별 threshold 재검증
4. 사용자별 Baseline 영속 저장 및 갱신 이력 관리
5. Node Health Monitoring
6. HMM 기반 모호한 Activity 추론 확장
