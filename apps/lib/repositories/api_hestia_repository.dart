import 'dart:async';

import '../core/constants/app_config.dart';
import '../core/network/hestia_exception.dart';
import '../models/context_state.dart';
import '../models/device.dart';
import '../models/explanation.dart';
import '../models/home_setup.dart';
import '../models/medication.dart';
import '../models/notification_item.dart';
import '../models/room.dart';
import '../models/user_preferences.dart';
import '../models/weather.dart';
import '../services/api_client.dart';
import '../services/realtime_client.dart';
import 'hestia_repository.dart';

/// HESTIA FastAPI(`/api/v1`) 기반 Repository.
///
/// Mock과 같은 인터페이스를 구현하므로 Widget/Controller는 바뀌지 않는다.
/// 변경은 WS /ws/monitor 로 바로 알아채고, 주기 조회는 연결이 끊겼을 때를 대비해 둔다.
class ApiHestiaRepository implements HestiaRepository {
  ApiHestiaRepository(
    this._client, {
    this.pollInterval = const Duration(seconds: 5),
  });

  factory ApiHestiaRepository.fromConfig() => ApiHestiaRepository(
        ApiClient(
          baseUrl: AppConfig.apiBaseUrl,
          timeout: AppConfig.requestTimeout,
        ),
        pollInterval: AppConfig.notificationPollInterval,
      );

  static const _api = 'api/v1';

  final ApiClient _client;
  final Duration pollInterval;

  // ---------------------------------------------------------------- setup

  @override
  Future<HomeSetup?> getSetup() => _orNull(() async =>
      HomeSetup.fromJson(_map(await _client.get('$_api/setup'))));

  /// devices는 DeviceIn 형태로 보낸다. status/online은 서버가 MQTT로 채우는 값이라 빼고,
  /// virtualId는 비워 두면 서버가 같은 종류·공간의 장치에 연결한다.
  @override
  Future<void> saveSetup(HomeSetup setup) async {
    await _client.put('$_api/setup', {
      ...setup.toJson(),
      'devices': [
        for (final d in setup.devices)
          {
            'id': d.id,
            'name': d.name,
            'type': d.type.wireName,
            'roomId': d.roomId,
          },
      ],
    });
  }

  // ---------------------------------------------------------------- home

  @override
  Future<List<Room>> getRooms() async =>
      _list(await _client.get('$_api/rooms')).map(Room.fromJson).toList();

  @override
  Future<List<Device>> getDevices() async =>
      _list(await _client.get('$_api/devices')).map(Device.fromJson).toList();

  @override
  Future<HestiaContext> getCurrentContext() async {
    final json = _map(await _client.get('$_api/context/current'));
    final context = HestiaContext.fromJson(json);
    return HestiaContext(states: context.states, updatedAt: DateTime.now());
  }

  // ---------------------------------------------------------------- notify

  @override
  Future<List<HestiaNotification>> getNotifications() async =>
      _list(await _client.get('$_api/notifications'))
          .map(HestiaNotification.fromJson)
          .toList();

  @override
  Future<void> acknowledgeNotification(
    String notificationId, {
    AckType ackType = AckType.seen,
  }) async {
    await _client.post(
      '$_api/notifications/${Uri.encodeComponent(notificationId)}/ack',
      {'ackType': ackType.wireName},
    );
  }

  // ---------------------------------------------------------------- explain

  @override
  Future<Explanation?> getLatestExplanation() => _orNull(() async =>
      Explanation.fromJson(_map(await _client.get('$_api/explanations/latest'))));

  @override
  Future<Explanation?> getExplanation(String explanationId) =>
      _orNull(() async => Explanation.fromJson(_map(await _client
          .get('$_api/explanations/${Uri.encodeComponent(explanationId)}'))));

  // ---------------------------------------------------------------- prefs

  @override
  Future<UserPreferences> getPreferences() async =>
      UserPreferences.fromJson(_map(await _client.get('$_api/preferences')));

  @override
  Future<void> savePreferences(UserPreferences preferences) async {
    await _client.put('$_api/preferences', preferences.toJson());
  }

  // ---------------------------------------------------------------- medication

  @override
  Future<List<Medication>> getMedications() async =>
      _list(await _client.get('$_api/medications'))
          .map(Medication.fromJson)
          .toList();

  @override
  Future<Medication> addMedication(Medication medication) async =>
      Medication.fromJson(
          _map(await _client.post('$_api/medications', medication.toJson())));

  @override
  Future<Medication> updateMedication(Medication medication) async =>
      Medication.fromJson(_map(await _client.put(
          '$_api/medications/${Uri.encodeComponent(medication.id)}',
          medication.toJson())));

  @override
  Future<void> deleteMedication(String medicationId) async {
    await _client
        .delete('$_api/medications/${Uri.encodeComponent(medicationId)}');
  }

  // ---------------------------------------------------------------- weather

  @override
  Future<Weather?> getWeather() => _orNull(() async =>
      Weather.fromJson(_map(await _client.get('$_api/weather'))));

  @override
  Future<void> setWeatherDemo(bool heat) async {
    await _client.put('$_api/weather/demo', {'heat': heat});
  }

  // ---------------------------------------------------------------- polling

  late final StreamController<HestiaNotification> _incoming =
      StreamController<HestiaNotification>.broadcast(
    onListen: _startPolling,
    onCancel: _stopPolling,
  );
  Timer? _timer;
  Set<String>? _knownIds;
  bool _polling = false;

  @override
  Stream<HestiaNotification> watchNotifications() => _incoming.stream;

  /// 화면에 보이는 값을 바꾸는 이벤트. 잦은 센서 원본처럼 화면과 무관한 것은 뺀다.
  static const _changeEvents = {
    'snapshot', // 접속 직후 — 끊긴 동안 놓친 변경을 다시 읽는다
    'context_update',
    'device_state',
    'registry_update',
    'weather_update',
    'notification',
    'notification_ack',
    'notification_cancel',
    'data_changed',
  };

  /// 이벤트가 몰려도 이 간격에 한 번만 다시 읽는다.
  static const _changeWindow = Duration(milliseconds: 200);

  late final RealtimeClient _realtime = RealtimeClient(
    url: _client.baseUri.resolve('ws/monitor').replace(
          scheme: _client.baseUri.scheme == 'https' ? 'wss' : 'ws',
        ),
    timeout: _client.timeout,
    onEvent: _onRealtimeEvent,
  );
  late final StreamController<void> _changes =
      StreamController<void>.broadcast(
    onListen: () => _realtime.start(),
    onCancel: () => _realtime.stop(),
  );
  Timer? _changeTimer;
  bool _pollAgain = false;

  @override
  Stream<void> watchChanges() => _changes.stream;

  void _onRealtimeEvent(String type) {
    if (!_changeEvents.contains(type)) return;
    if (type == 'notification') {
      // 배너를 다음 주기까지 기다리지 않는다.
      if (_polling) {
        _pollAgain = true;
      } else {
        _poll();
      }
    }
    _changeTimer ??= Timer(_changeWindow, () {
      _changeTimer = null;
      if (!_changes.isClosed) _changes.add(null);
    });
  }

  void _startPolling() {
    _timer ??= Timer.periodic(pollInterval, (_) => _poll());
    _poll();
  }

  void _stopPolling() {
    _timer?.cancel();
    _timer = null;
  }

  /// 처음 읽은 목록은 기준으로만 쓰고, 이후 새로 생긴 id만 내보낸다.
  Future<void> _poll() async {
    if (_polling) return;
    _polling = true;
    try {
      final items = await getNotifications();
      final known = _knownIds;
      _knownIds = {...?known, ...items.map((n) => n.id)};
      if (known == null) return;
      for (final n in items.reversed) {
        if (!known.contains(n.id) && !_incoming.isClosed) _incoming.add(n);
      }
    } on HestiaException {
      // 연결이 끊겼다. 다음 주기에 다시 시도한다.
    } finally {
      _polling = false;
      if (_pollAgain && !_incoming.isClosed) {
        _pollAgain = false;
        _poll();
      }
    }
  }

  void dispose() {
    _stopPolling();
    _incoming.close();
    _realtime.stop();
    _changeTimer?.cancel();
    _changes.close();
    _client.close();
  }

  // ---------------------------------------------------------------- helpers

  /// 404는 "아직 없음"으로 본다 (설정 전, 판단 없음, 날씨 수신 전).
  static Future<T?> _orNull<T>(Future<T> Function() call) async {
    try {
      return await call();
    } on HestiaHttpException catch (e) {
      if (e.isNotFound) return null;
      rethrow;
    }
  }

  static Map<String, dynamic> _map(Object? json) {
    if (json is Map<String, dynamic>) return json;
    throw const HestiaException('서버 응답 형식이 올바르지 않습니다.');
  }

  static List<Map<String, dynamic>> _list(Object? json) {
    if (json is List && json.every((e) => e is Map<String, dynamic>)) {
      return json.cast<Map<String, dynamic>>();
    }
    throw const HestiaException('서버 응답 형식이 올바르지 않습니다.');
  }
}
