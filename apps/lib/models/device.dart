import 'room.dart';

/// HESTIA가 관리하는 가전 종류.
///
/// 서버/설정에서 모르는 값이 와도 깨지지 않도록 [unknown]으로 받는다.
enum DeviceType {
  tv('TV', 'TV', ['living']),
  light('LIGHT', '조명', ['living', 'kitchen', 'bedroom']),
  airConditioner('AIR_CONDITIONER', '에어컨', ['living']),
  airPurifier('AIR_PURIFIER', '공기청정기', ['living']),
  refrigerator('REFRIGERATOR', '냉장고', ['kitchen']),
  washer('WASHER', '세탁기', ['utility']),
  waterPurifier('WATER_PURIFIER', '정수기', ['kitchen']),
  robotCleaner('ROBOT_CLEANER', '로봇청소기', ['living']),
  unknown('UNKNOWN', '기타 가전', []);

  const DeviceType(this.wireName, this.label, this.allowedRoomIds);

  /// API/MQTT에서 쓰는 문자열.
  final String wireName;
  final String label;

  /// 둘 수 있는 공간. config/homes/demo.toml 의 [[devices]] area 와 맞춘다.
  /// 엔진에 없는 (종류, 공간) 조합은 다른 공간의 장치에 잘못 연결된다.
  /// 첫 항목이 기본 위치다.
  final List<String> allowedRoomIds;

  /// [rooms] 중 이 가전을 둘 수 있는 공간.
  List<Room> allowedRooms(List<Room> rooms) =>
      rooms.where((r) => allowedRoomIds.contains(r.id)).toList();

  static DeviceType fromWire(String? value) {
    for (final t in values) {
      if (t.wireName == value) return t;
    }
    return unknown;
  }

  /// 공간마다 하나씩 자동으로 두는 가전. 고르거나 배치하지 않는다.
  /// demo.toml 처럼 조명은 거실·주방·침실에 한 대씩 있다.
  bool get perRoom => this == light;

  /// 사용자가 고를 수 있는 가전 목록.
  static List<DeviceType> get selectable =>
      values.where((t) => t != unknown && !t.perRoom).toList();

  /// [rooms] 에 자동으로 두는 가전. id 는 공간마다 고정이라 저장해도 상태가 이어진다.
  static List<Device> perRoomDevices(List<Room> rooms) => [
        for (final type in values.where((t) => t.perRoom))
          for (final room in type.allowedRooms(rooms))
            Device(
              id: '${type.wireName.toLowerCase()}-${room.id}',
              name: '${room.name} ${type.label}',
              type: type,
              roomId: room.id,
            ),
      ];
}

/// 가전의 마지막 상태. Context Engine/Device가 보고한 값을 그대로 담는다.
class DeviceStatus {
  const DeviceStatus({
    required this.state,
    this.attributes = const {},
  });

  /// ON / OFF / STANDBY / AUTO / RUNNING / DONE 등.
  final String state;

  /// 장치별 추가 값 (예: targetTemp: 24).
  final Map<String, dynamic> attributes;

  static const unknown = DeviceStatus(state: 'UNKNOWN');

  factory DeviceStatus.fromJson(Object? json) {
    if (json is String) return DeviceStatus(state: json);
    if (json is Map<String, dynamic>) {
      return DeviceStatus(
        state: (json['state'] ?? 'UNKNOWN').toString(),
        attributes: Map<String, dynamic>.from(json)..remove('state'),
      );
    }
    return unknown;
  }

  Map<String, dynamic> toJson() => {'state': state, ...attributes};
}

class Device {
  const Device({
    required this.id,
    required this.name,
    required this.type,
    required this.roomId,
    this.status = DeviceStatus.unknown,
    this.online = true,
  });

  final String id;
  final String name;
  final DeviceType type;
  final String roomId;
  final DeviceStatus status;
  final bool online;

  Device copyWith({
    String? name,
    String? roomId,
    DeviceStatus? status,
    bool? online,
  }) =>
      Device(
        id: id,
        name: name ?? this.name,
        type: type,
        roomId: roomId ?? this.roomId,
        status: status ?? this.status,
        online: online ?? this.online,
      );

  factory Device.fromJson(Map<String, dynamic> json) => Device(
        id: json['id'] as String,
        name: json['name'] as String,
        type: DeviceType.fromWire(json['type'] as String?),
        roomId: (json['roomId'] ?? '') as String,
        status: DeviceStatus.fromJson(json['status']),
        online: json['online'] as bool? ?? false,
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'type': type.wireName,
        'roomId': roomId,
        'status': status.toJson(),
        'online': online,
      };
}
