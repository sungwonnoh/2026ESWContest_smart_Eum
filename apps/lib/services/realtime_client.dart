import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// WS /ws/monitor 구독. Repository만 쓰고 Widget은 직접 쓰지 않는다.
///
/// 이벤트 내용은 해석하지 않고 종류(type)만 알린다. 값은 REST로 다시 읽는다.
/// 연결이 끊기면 [retryDelay]마다 다시 붙고, 접속하면 서버가 snapshot을 보낸다.
class RealtimeClient {
  RealtimeClient({
    required this.url,
    required this.onEvent,
    this.timeout = const Duration(seconds: 5),
    this.retryDelay = const Duration(seconds: 3),
  });

  final Uri url;
  final void Function(String type) onEvent;
  final Duration timeout;
  final Duration retryDelay;

  bool _running = false;
  bool _connecting = false;
  WebSocket? _socket;
  Timer? _retry;

  void start() {
    if (_running) return;
    _running = true;
    _connect();
  }

  void stop() {
    _running = false;
    _retry?.cancel();
    _retry = null;
    _socket?.close();
    _socket = null;
  }

  Future<void> _connect() async {
    if (_connecting || _socket != null) return;
    _connecting = true;
    final http = HttpClient()..connectionTimeout = timeout;
    try {
      final socket = await WebSocket.connect(url.toString(), customClient: http)
          .timeout(timeout);
      http.close();
      if (!_running) {
        await socket.close();
        return;
      }
      // 응답 없는 연결도 끊긴 것으로 알아채도록 ping을 보낸다.
      socket.pingInterval = timeout;
      _socket = socket;
      socket.listen(
        _onMessage,
        onDone: _onClosed,
        onError: (Object _) => _onClosed(),
        cancelOnError: true,
      );
    } on Exception {
      http.close(force: true);
      _scheduleRetry();
    } finally {
      _connecting = false;
    }
  }

  void _onMessage(dynamic message) {
    if (message is! String) return;
    try {
      final event = jsonDecode(message);
      if (event is Map && event['type'] is String) {
        onEvent(event['type'] as String);
      }
    } on FormatException {
      // 해석할 수 없는 메시지는 버린다.
    }
  }

  void _onClosed() {
    _socket = null;
    _scheduleRetry();
  }

  void _scheduleRetry() {
    if (!_running) return;
    _retry?.cancel();
    _retry = Timer(retryDelay, _connect);
  }
}
