# HESTIA Smart Home

> 제24회 임베디드 소프트웨어 경진대회 출품 프로젝트

HESTIA는 센서와 생활 로그를 바탕으로 사용자의 현재 상태를 추론하고, 개인별 생활 패턴과 상황을 함께 고려해 필요한 시점에만 개입하는 초개인화 스마트홈 시스템 입니다.


---

## Architecture

```text
ESP32 / Tasmota
  │
  │ Sensor / Device Event
  ▼
Raspberry Pi 5
  ├─ MQTT Broker (Mosquitto)
  ├─ Context Engine     World State / FSM / Policy / Notify
  ├─ FastAPI Backend    App API, SQLite, Caregiver Telegram
  ├─ TTS                Voice prompt
  └─ Flutter App (IPK)
  │                ▲
  │ hestia/log/t0  │ hestia/model/kde (retained)
  │                │ hestia/external/weather
  ▼                │
Raspberry Pi 4
  ├─ t0 Logger          t0_log.jsonl
  ├─ Learning Engine    KDE Batch Learning (daily 03:00)
  └─ Weather Service
```

- **ESP32 / Tasmota**: 센서 수집, 가전 전력 측정 및 제어, 물리 입력
- **Raspberry Pi 5**: MQTT Broker, Context Engine(실시간 상태 추론·Policy 판단·알림), FastAPI Backend, TTS 음성 안내, Flutter 앱
- **Raspberry Pi 4**: t0 로그 수집, 개인별 KDE Baseline 배치 학습, 날씨 서비스
- **MQTT**: 장치와 서비스 간 이벤트 및 모델 결과 전달

---

# Project Structure

```text
hestia-smart-home/
│
├── apps/                           # Flutter 앱 (RPi5 터치스크린, ATLAS IPK)
├── backend/                        # FastAPI Backend (RPi5) — 앱 API, SQLite, 보호자 텔레그램
├── config/                         # Context Engine 정책·집 구성 (policy.toml, homes/)
├── data/
│   ├── raw/                        # CASAS 원본 (GitHub 미포함)
│   ├── processed/                  # Aruba 전처리 결과
│   └── synthetic/                  # 합성 사용자 p1 / p2 t0 로그
├── deploy/                         # systemd 유닛, 환경변수 예시, 시연 스크립트(demo/)
├── docs/                           # 시스템 설계, 개발 문서
├── firmware/
│   └── esp32-1/                    # ESP32 센서 노드 펌웨어 (PlatformIO)
├── prototype/                      # 초기 Context Engine 프로토타입 (현재는 services/context-engine 이 대체)
├── schemas/
│   └── mqtt/                       # MQTT Topic / Payload Schema
├── scripts/
│   └── datasets/                   # CASAS 전처리, 합성 데이터 생성
├── services/
│   ├── context-engine/             # RPi5 실시간 Context Engine
│   ├── learning-engine/            # RPi4 KDE 학습, t0 로거
│   ├── tasmota-bridge/             # Tasmota 스마트플러그(전력)·조명 ↔ HESTIA MQTT
│   └── weather/                    # RPi4 기상청 날씨 서비스
├── .gitignore
└── README.md
```

각 서비스의 테스트는 서비스 폴더 안 `tests/` 에 있습니다.

---

## Dataset

개인 생활 패턴 Baseline 개발·검증에는 **CASAS Smart Home Dataset**(Aruba, Milan, Tulum2, Cairo)과
합성 사용자 로그(p1 / p2)를 사용합니다.

- Dataset: https://zenodo.org/records/17180309
- CASAS 원본은 GitHub에 포함하지 않습니다. Aruba 전처리 결과와 합성 로그는 포함합니다.
- 다운로드 및 전처리 방법은 [`docs/dataset-setup.md`](docs/dataset-setup.md)를 참고하세요.

## Learning Engine

Learning Engine은 기상·취침·식사 시각으로 Gaussian KDE 기반 개인 Baseline을 생성합니다.
수분 섭취(`hydration`)는 현재 KDE 분포가 아니라 Context Engine의 간격 규칙으로 처리합니다.
운영에서는 RPi5의 t0 로그를 RPi4가 저장하고 하루 한 번 모델을 갱신합니다.

예:

```bash
python3 services/learning-engine/baseline.py
python3 services/learning-engine/debug_kde_query.py --time 09:40
```

실행 옵션과 검증 결과는 [`services/learning-engine/README.md`](services/learning-engine/README.md)를 참고하세요.

상세 설명은 [`docs/learning-engine.md`](docs/learning-engine.md)를 참고하세요.

## Context Engine

Context Engine은 MQTT 이벤트를 받아 현재 World State를 관리하고,
Rule / FSM / 개인 Baseline을 결합해 최종 개입 여부를 판단합니다.

전체 구조는 [`docs/architecture.md`](docs/architecture.md)를 참고하세요.

## Development Status

현재 주요 개발 방향:

- 실제 사용자 로그 품질 검증 (60~90일, 시간순 holdout)
- Predictability / 알림 threshold 재검증
- 사용자별 Baseline 영속 저장
- Node Health Monitoring
- HMM 기반 모호한 Activity 추론

세부 진행 상황은 [`docs/development-status.md`](docs/development-status.md)를 참고하세요.

---

## License

이 프로젝트의 소스코드는 [MIT License](LICENSE)로 공개합니다.
누구나 자유롭게 사용·수정·재배포할 수 있으며, 저작권 표시와 라이선스 전문을 함께 포함해야 합니다.

Copyright (c) 2026 HESTIA Team (sungwonnoh, kim-hawon)

### Dataset

- **CASAS Smart Home Dataset** (Aruba, Milan, Tulum2, Cairo) — [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
  - 출처: https://zenodo.org/records/17180309
  - `data/processed/` 의 Aruba 전처리 결과는 원본을 가공한 파생 데이터입니다.
  - D. J. Cook, A. S. Crandall, B. L. Thomas, N. C. Krishnan, "CASAS: A Smart Home in a Box," *IEEE Computer*, 46(7), 62–69, 2013.
- **합성 사용자 로그** (`data/synthetic/` p1 / p2) — 팀이 직접 생성한 데이터로, 소스코드와 같은 MIT License를 따릅니다.

### Third-party Open Source

아래 오픈소스를 의존성으로 사용하며, 각 라이브러리는 자체 라이선스를 따릅니다. 이 레포에 해당 소스는 포함하지 않습니다.

| 영역 | 오픈소스 | 라이선스 |
|---|---|---|
| ESP32 펌웨어 | ArduinoJson, espMqttClient, Adafruit DHT sensor library, BH1750 | MIT |
| Backend / 엔진 | FastAPI, pydantic | MIT |
| Backend / 엔진 | uvicorn, httpx, NumPy, SciPy | BSD |
| MQTT | paho-mqtt, Eclipse Mosquitto | EPL-2.0 / EDL-1.0 |
| 앱 | Flutter, cupertino_icons | BSD-3-Clause / MIT |
| 스마트플러그 | Tasmota (기기 펌웨어로만 사용, 코드 미포함) | GPL-3.0 |
| TTS | eSpeak NG (RPi5 Docker 컨테이너에 패키지로 설치해 별도 프로그램으로 실행, 코드 미포함) | GPL-3.0-or-later |
