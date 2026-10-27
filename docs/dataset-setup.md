# Dataset Setup

## Dataset

KDE Baseline 개발·검증에는 **CASAS Smart Home Dataset**을 사용합니다.

Aruba는 1인 거주자의 장기간 스마트홈 센서 및 Activity Label 데이터로,
사용자 개인의 생활 시간 패턴을 검증하기 위한 초기 데이터셋입니다.

Dataset:

https://zenodo.org/records/17180309

다운로드 파일:

```text
new_labeled_data.zip
```

압축 파일에는 다음 데이터가 포함됩니다.

```text
aruba.txt
cairo.txt
milan.txt
tulum1.txt
tulum2.txt
```

HESTIA에서 쓰는 파일과 용도:

| 파일 | 거주 | 용도 |
|---|---|---|
| `aruba.txt` | 1인 | 식사 KDE 기본 검증 (breakfast proxy, 식사 묶음), 수면·기상 proxy |
| `milan.txt` | 1인 | 수면·기상 proxy, predictability 비교 (`casas.py`) |
| `tulum2.txt` | 2인 | 수면·기상 proxy, predictability 비교 (`casas.py`) |
| `cairo.txt` | 2인 | 수면·기상 proxy, predictability 비교 (`casas.py`) |

---

## 1. 데이터 폴더 생성

레포지토리 루트에서 실행합니다.

```bash
mkdir -p data/raw/casas/aruba
mkdir -p data/processed/aruba
```

---

## 2. 데이터 다운로드

Zenodo에서 `new_labeled_data.zip`을 다운로드한 뒤 다음 위치로 이동합니다.

```text
data/raw/casas/aruba/new_labeled_data.zip
```

macOS 예시:

```bash
mv ~/Downloads/new_labeled_data.zip data/raw/casas/aruba/
```

---

## 3. 압축 해제

```bash
cd data/raw/casas/aruba
unzip new_labeled_data.zip
```

레포 루트로 돌아갑니다.

```bash
cd ../../../..
```

---

## 4. Meal Preparation 추출

Aruba 원본 데이터에서 다음 Activity의 시작 이벤트를 추출합니다.

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

현재 데이터 기준 약 1,606개의 `Meal_Preparation begin` 이벤트가 추출됩니다.

---

## 5. Breakfast Proxy 생성

CASAS에는 `Breakfast_Preparation`이라는 별도 라벨이 없기 때문에
HESTIA MVP에서는 다음 규칙으로 아침 준비 시각을 정의합니다.

```text
05:00 ~ 11:00 사이에 발생한
하루의 첫 번째 Meal_Preparation
```

실행:

```bash
python3 scripts/datasets/filter_breakfast.py
```

출력:

```text
data/processed/aruba/breakfast_preparation.csv
```

현재 Aruba 기준 약 212일의 아침 대표 이벤트가 생성됩니다.

---

## Data Version Control

CASAS 원본(`data/raw/`)은 GitHub에 업로드하지 않습니다 (`.gitignore`). 이 문서의 절차로 다시 받습니다.

저장소에 포함된 데이터:

| 경로 | 내용 |
|---|---|
| `data/processed/aruba/meal_preparation.csv` | Aruba `Meal_Preparation begin` 추출 결과 |
| `data/processed/aruba/breakfast_preparation.csv` | Breakfast proxy (기본 `baseline.py` 입력) |
| `data/synthetic/p1_p2/p1_t0_log.jsonl`, `p2_t0_log.jsonl` | 합성 사용자 p1 / p2 의 91일 t0 로그 (Context Engine 형식) |
| `data/synthetic/p1_p2.sqlite` | 위 합성 로그를 RPi4용 SQLite 이벤트 DB로 만든 것 (`scripts/datasets/build_synthetic_sqlite.py`) |

---

## Current Limitation

현재 `breakfast_preparation.csv`는 실제 `Breakfast` 라벨이 아니라
다음 규칙으로 생성한 MVP용 Proxy 데이터입니다.

```text
05:00 ~ 11:00
+
하루 첫 Meal_Preparation begin
```

따라서 Aruba 기반 KDE는 실제 사용자 baseline이 아니라 개발·검증용 모델입니다.
전체 식사 묶음은 아침·점심·저녁이 섞인 multi-modal distribution이므로,
현재 Learning Engine은 peak와 끼니별 predictability를 함께 계산합니다.

현재는 breakfast / lunch / dinner를 포함한 전체 Meal 이벤트를 이용해
`meal_time`의 multi-modal peak와 끼니별 predictability를 계산합니다.

## 실제 사용자 데이터로 전환할 때 필요한 것

실제 사용자 데이터를 넣으면 개인별 패턴을 학습할 수 있고 Aruba의 proxy 라벨 문제도
줄어듭니다. 그러나 데이터 파일을 교체하는 것만으로 실제 알림 신뢰성이 보장되지는 않습니다.

- 최소 60~90일의 연속 로그를 확보하고, 가능하면 3~6개월까지 누적합니다.
- 식사별 준비 시각(`t0`)과 실제 섭취 시각(`eat_t0`)의 의미를 일정하게 유지합니다.
- 식사하지 않은 날, 외출일, 결측일을 기록해 정상 누락과 데이터 결측을 구분합니다.
- 수면 시작·종료를 연결하고 센서 confidence·오탐·prompted 여부를 보존합니다.
- 평일/주말 및 재실/외출을 보존해 조건별 패턴을 검증합니다.
- 최근 데이터를 시간순으로 별도 보류해 실제 알림 오탐률·누락률을 평가합니다.

즉 실제 데이터는 필요한 조건이지만 충분조건은 아닙니다. 기간과 이벤트 품질을 함께
검증해야 “실제 알림을 신뢰하기에는 부족하다”는 현재의 한계를 해소할 수 있습니다.
