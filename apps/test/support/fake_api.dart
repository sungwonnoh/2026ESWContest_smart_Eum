import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// FastAPI 대신 응답을 돌려주는 로컬 HTTP 서버.
class FakeApi {
  late HttpServer _server;
  bool _stopped = false;
  final routes = <String, (int, Object?)>{};
  final requests = <(String, String, String)>[];
  final _sockets = <WebSocket>{};

  int get port => _server.port;

  int get socketCount => _sockets.length;

  Future<void> start() async {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server.listen((req) async {
      if (req.uri.path == '/ws/monitor' &&
          WebSocketTransformer.isUpgradeRequest(req)) {
        final socket = await WebSocketTransformer.upgrade(req);
        _sockets.add(socket);
        socket.listen(null, onDone: () => _sockets.remove(socket));
        socket.add(jsonEncode({'type': 'snapshot'}));
        return;
      }
      final body = await utf8.decoder.bind(req).join();
      requests.add((req.method, req.uri.path, body));
      final (status, json) =
          routes['${req.method} ${req.uri.path}'] ?? (404, {'detail': 'not found'});
      req.response
        ..statusCode = status
        ..headers.contentType = ContentType.json
        ..write(jsonEncode(json));
      await req.response.close();
    });
  }

  /// 연결된 모든 클라이언트에 Monitor 이벤트를 보낸다.
  void pushEvent(String type) {
    for (final socket in _sockets) {
      socket.add(jsonEncode({'type': type}));
    }
  }

  /// 서버 재시작처럼 연결만 끊는다.
  Future<void> dropSockets() async {
    for (final socket in _sockets.toList()) {
      await socket.close();
    }
  }

  Future<void> stop() async {
    if (_stopped) return;
    _stopped = true;
    await dropSockets();
    await _server.close(force: true);
  }
}
