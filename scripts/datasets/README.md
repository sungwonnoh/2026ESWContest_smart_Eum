# Dataset Preprocessing

> CASAS Aruba 전처리(1~2)와 합성 사용자 데이터 생성(3) 스크립트입니다.

HESTIA의 개인 생활 패턴 Baseline 학습을 위해 CASAS Aruba 데이터를 전처리하는 스크립트입니다.

## 목적

CASAS Aruba의 원본 생활 로그에서 `Meal_Preparation` 활동 시작 시각을 추출하고, 아침 시간대의 대표 이벤트를 생성합니다.

최종적으로 KDE 기반 개인 생활 패턴 분석에서 사용할 다음 데이터를 생성합니다.

```text
data/processed/aruba/breakfast_preparation.csv
```

## 데이터

사용 데이터:

* CASAS Smart Home Dataset
* Home: Aruba
* Resident: 1인
* 기간: 2010~2011
* Activity Label 포함

원본 파일 위치:

```text
data/raw/casas/aruba/aruba.txt
```

## 전처리 흐름

```text
aruba.txt
    ↓
Meal_Preparation begin 추출
    ↓
meal_preparation.csv
    ↓
05:00 ~ 11:00 필터링
    ↓
날짜별 첫 번째 이벤트 선택
    ↓
breakfast_preparation.csv
```

## 1. Aruba Parser

파일:

```text
scripts/datasets/parse_aruba.py
```

`aruba.txt`에서 다음 이벤트를 추출합니다.

```text
Meal_Preparation begin
```

실행:

```bash
python3 scripts/datasets/parse_aruba.py
```

출력:

```text
data/processed/aruba/meal_preparation.csv
```

현재 Aruba 데이터 기준 약 1,606개의 `Meal_Preparation begin` 이벤트가 추출됩니다.

## 2. Breakfast Filter

파일:

```text
scripts/datasets/filter_breakfast.py
```

전체 Meal Preparation 이벤트에서 다음 조건으로 아침 대표 이벤트를 생성합니다.

* 시간 범위: `05:00 <= time < 11:00`
* 같은 날짜에 여러 이벤트가 존재하면 가장 첫 이벤트 사용

실행:

```bash
python3 scripts/datasets/filter_breakfast.py
```

출력:

```text
data/processed/aruba/breakfast_preparation.csv
```

현재 Aruba 데이터 기준 약 212일의 아침 대표 이벤트가 생성됩니다.

## 출력 예시

```csv
date,time,activity
2010-11-04,08:11:09.966157,Breakfast_Preparation
2010-11-05,07:42:31.123456,Breakfast_Preparation
```

## 주의

`Breakfast_Preparation`은 CASAS에서 직접 제공하는 라벨이 아닙니다.

HESTIA MVP에서는 다음 조건을 만족하는 이벤트를 아침 준비의 proxy로 정의합니다.

```text
05:00~11:00 사이의 첫 Meal_Preparation 이벤트
```

따라서 향후 실제 사용자 센서 데이터 또는 보다 세분화된 Activity Recognition 모델로 교체할 수 있습니다.

## 3. 합성 사용자 데이터 (p1 / p2)

실제 사용자 로그 대신 쓰는 합성 페르소나입니다. Context Engine t0 형식(`meal` / `sleep_start` / `sleep_end` / `hydration`)으로 만듭니다.

| 페르소나 | 특징 |
|---|---|
| p1 | 78세 독거, 규칙적. 밤중 화장실 평균 1회, 마지막 2주는 2회 (야간뇨 증가 시연) |
| p2 | 78세 독거, 야간뇨 없음. 아침을 거르고 점심(11시)·저녁(17시)만 먹음 (시연용) |

```bash
# JSONL 하나 (페르소나별)
python3 services/learning-engine/synth_t0.py --persona p2 --output /tmp/p2_t0_log.jsonl

# p1 / p2 JSONL + RPi4용 SQLite 이벤트 DB
python3 scripts/datasets/build_synthetic_sqlite.py \
    --output data/synthetic/p1_p2.sqlite --jsonl-dir data/synthetic/p1_p2
```

저장소에는 `data/synthetic/p1_p2/` (91일) 결과가 포함되어 있습니다.
