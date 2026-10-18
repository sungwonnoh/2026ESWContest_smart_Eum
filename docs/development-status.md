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
- [ ] 실제 RPi MQTT E2E 테스트
- [ ] retained model 재수신 검증
- [ ] HOME / AWAY 상태 검출
- [ ] meal_done 상태 연결
- [ ] FSM / World State와 Meal Policy 통합
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
- [x] recent weighting / prompted 감쇠 / cold-start blending
- [x] Replay용 A/B KDE 모델 생성
- [ ] 실제 RPi4 → MQTT → RPi5 Publish 및 retained 재수신 E2E 검증
- [ ] Predictability Threshold 재검증
- [x] 전체 `meal_time` multi-modal peak 계산
- [ ] 사용자별 Baseline 영속 저장
- [x] 주기적 Batch Learning 구현
- [ ] HMM 기반 모호한 Activity 추론
- [ ] 실제 사용자 로그 60~90일 품질·시간순 holdout 검증

## Current Meal KDE Flow

```text
CASAS Aruba
  ↓
Meal_Preparation begin
  ↓
Breakfast Proxy
  ↓
Gaussian KDE
  ↓
24h / 15min / 96-bin density
  ↓
Predictability
  ↓
MQTT Model Payload
  ↓
hestia/model/kde
  ↓
ContextEngine.ingest()
  ↓
ModelStore
  ↓
Tail Probability
  ↓
Meal Policy
  ↓
Intervention Candidate
```

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

실행:

```bash
python3 -m pytest tests/context-engine
```

현재 결과:

```text
20 passed
```

## Next Priorities

1. 실제 RPi4 → MQTT → RPi5 KDE E2E 및 retained 재수신 테스트
2. 실제 사용자 로그의 confidence·결측·오탐 품질 지표 수집
3. 최소 60~90일 데이터의 시간순 holdout 검증
4. 평일/주말·재실/외출·끼니별 threshold 재검증
5. 사용자별 Baseline 영속 저장 및 갱신 이력 관리
6. HOME / AWAY, meal_done 등 World State와 Meal Policy 연결
7. HMM 기반 모호한 Activity 추론 확장
