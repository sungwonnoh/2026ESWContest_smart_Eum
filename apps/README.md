# HESTIA Flutter App

HESTIA 스마트홈의 RPi5 터치스크린용 Flutter 앱.
RPi5에서 FastAPI Backend(`http://127.0.0.1:8000`)와 같은 장치에서 실행한다.

## 데이터 소스

```text
Mock Data / FastAPI ─→ HestiaRepository ─→ Controller(ChangeNotifier) ─→ Flutter UI
```

| `HESTIA_DATA_SOURCE` | 구현체 | 용도 |
|---|---|---|
| `mock` (기본) | `MockHestiaRepository` | 서버 없이 UI 확인 |
| `api` | `ApiHestiaRepository` | RPi5 FastAPI 연동. 새 알림은 5초 polling |

- 판단(사용자 상태 추론)은 Context Engine이 한다. 앱은 받은 상태를 표시만 한다.
- Widget은 HTTP/MQTT에 직접 접근하지 않는다. 데이터 소스는 Repository 구현체로만 바꾼다.
- 빌드 시 `--dart-define` 으로 주입한다.
  - `HESTIA_DATA_SOURCE=api` — FastAPI 연동 (없으면 Mock)
  - `API_BASE_URL=...` — 기본 `http://127.0.0.1:8000`
  - `APP_VERSION=...` — 설정 > 시스템 정보의 버전. IPK 버전(`atlas/appinfo.json`의 `version`)과 같은 값을 넣는다.
    주입하지 않으면 `AppConfig.appVersion` 기본값이 보이므로, 릴리스 때 기본값과 `pubspec.yaml` 버전도 함께 올린다.

## 구조

```text
lib/
├── app/           앱 셸, 라우터, 테마, 의존성 Scope
├── core/          상수, 예외, 공통 위젯, 표시용 변환
├── models/        도메인 모델 (JSON 직렬화 포함)
├── repositories/  HestiaRepository 인터페이스 + Mock / API 구현
└── features/      splash / onboarding / home / notifications / medication / explanation / settings
```

## 실행

```bash
flutter pub get
flutter analyze
flutter test
flutter run -d macos   # MacBook에서 UI 확인용
```

MacBook 실행 성공은 ATLAS/RPi5 실행 성공을 의미하지 않는다.
RPi5 검증은 `hestia_flutter_test`(ATLAS 프로젝트)에 소스를 반영해 `flutter-atlas`로 수행한다.

## Git 제외 대상

`atlas/`, `build/`, `.dart_tool/`, `.idea/`, `.metadata`, `*.ipk`는 추적하지 않는다.
