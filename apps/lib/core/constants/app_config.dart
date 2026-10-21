/// 빌드 시 주입하는 설정.
///
/// 서버 주소는 코드에 고정하지 않는다.
/// `flutter run --dart-define=HESTIA_DATA_SOURCE=api --dart-define=API_BASE_URL=http://<host>:<port>`
abstract final class AppConfig {
  static const appName = 'HESTIA';
  /// 설정 > 시스템 정보에 보이는 버전. IPK 버전(atlas/appinfo.json)과 같아야 한다.
  /// 빌드 때 `--dart-define=APP_VERSION=<appinfo.json version>` 으로 주입한다.
  /// 주입하지 않은 빌드(로컬 실행·테스트)는 기본값을 쓴다 — 릴리스마다 pubspec 과 함께 올린다.
  static const appVersion = String.fromEnvironment(
    'APP_VERSION',
    defaultValue: '1.0.4',
  );

  /// 'mock': 서버 없이 Mock 데이터 (기본), 'api': HESTIA FastAPI
  static const dataSource = String.fromEnvironment(
    'HESTIA_DATA_SOURCE',
    defaultValue: 'mock',
  );

  static bool get useApi => dataSource == 'api';

  /// RPi5에서는 앱과 FastAPI가 같은 장치에 있으므로 기본값을 그대로 쓴다.
  static const apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://127.0.0.1:8000',
  );

  static const requestTimeout = Duration(seconds: 5);

  /// 실시간 연결(Phase 3) 전까지 새 알림을 확인하는 주기.
  static const notificationPollInterval = Duration(seconds: 5);
}
