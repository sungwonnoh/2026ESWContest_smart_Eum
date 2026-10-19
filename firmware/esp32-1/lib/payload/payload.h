#pragma once

#include <ArduinoJson.h>
#include <cstdint>
#include <string>

namespace hestia {

enum class SensorType : uint8_t { Presence, Motion, Door, Power, Bed, Light, Climate };

void putEnvelope(JsonDocument& doc,
                 const char* src_id,
                 uint32_t seq,
                 uint32_t sent_ts);

const char*  typeName(SensorType type);
uint8_t      qosFor(SensorType type);
bool         isRetained(SensorType type);
std::string   sensorStateTopic(const char* virtual_id);
std::string deviceStateTopic(const char* virtual_id);
std::string deviceEventTopic(const char* virtual_id);
std::string notifyAckTopic();

struct PresenceReading {
    bool     present        = false;
    uint8_t  energy         = 0;      // max(moving, static) — 기존 호환
    uint16_t distance_cm    = 0;

    bool     has_detail     = false;
    uint8_t  moving_energy  = 0;
    uint8_t  static_energy  = 0;
    uint8_t  target         = 0;      // 0 NONE / 1 MOVING / 2 STATIC / 3 BOTH
    uint16_t dist_spread_cm = 0;      // 최근 10초 거리 변동폭
};

std::string buildPresence(const char* src_id, uint32_t seq, uint32_t sent_ts, bool present, uint8_t energy, uint16_t distance_cm);
std::string buildPresence(const char* src_id, uint32_t seq, uint32_t sent_ts,
                          const PresenceReading& r);
std::string buildMotion(const char* src_id, uint32_t seq, uint32_t sent_ts, bool motion);
std::string buildDoor(const char* src_id, uint32_t seq, uint32_t sent_ts, bool open);
std::string buildPower(const char* src_id, uint32_t seq, uint32_t sent_ts, uint16_t watt);
std::string buildBed(const char* src_id, uint32_t seq, uint32_t sent_ts, bool occupied);
std::string buildLight(const char* src_id, uint32_t seq, uint32_t sent_ts, uint32_t illuminance_lux);
std::string buildClimate(const char* src_id, uint32_t seq, uint32_t sent_ts, float temperature_c, uint8_t humidity_pct);
std::string buildDisplayState(const char* src_id, uint32_t seq, uint32_t sent_ts,
                              const char* display,
                              const char* notify_id);       // display: 표시 중인 문구. 없으면 "" / notify_id: 표시 중인 알림 id. nullptr이면 필드를 넣지 않는다.

class SeqCounter {
public:
    static const uint8_t MAX_IDS = 12;

    SeqCounter();
    
    // virtual_id의 다음 seq를 out_seq에 담음
    // 처음 보는 id면 0부터 시작
    // 슬롯이 가득 차면 false를 반환하고 out_seq는 건드리지 않음
    bool next(const char* virtual_id, uint32_t& out_seq);

private:
    struct Slot {
        const char* id;
        uint32_t    seq;
    };

    Slot slots_[MAX_IDS];
    uint8_t count_;
};

//노드(esp32보드) status
std::string buildStatus(const char* node_id, uint32_t sent_ts, bool online, bool ts_synced);
std::string nodeStatusTopic(const char* node_id);
std::string nodeAnnounceTopic(const char* node_id);
std::string deviceCmdTopic(const char* virtual_id); 

//노드(esp32보드) announce
struct Emulated {
    const char* virtual_id;
    bool        is_device;      // true면 "device_type" 키, false면 "type" 키
    const char* type_name;      // "motion" 또는 "display_node"
};

// 생성 헬퍼 — 센서는 enum을 거쳐 오타를 막는다
Emulated sensorEmulated(const char* virtual_id, SensorType type);
Emulated deviceEmulated(const char* virtual_id, const char* device_type);

std::string buildAnnounce(const char* node_id, uint32_t seq, uint32_t sent_ts, const char* fw, const Emulated* items, uint8_t count);


struct SensorSlot {
    const char* virtual_id;
    SensorType  type;
    const char* kind;      // "pir", "ld2410c", "dht22" ...
    uint8_t     pin;
};

struct DeviceSlot {
    const char* virtual_id;
    const char* device_type;   // "smart_light", "display_node" ...
    uint8_t     pin;           // 255 = 핀 없음
};

// 문자열을 SensorType 으로. 모르는 값이면 false
bool parseSensorType(const char* name, SensorType& out);


// ── 알림 수신·확인 ───────────────────────────
extern const char* const ACK_DELIVERED;   // "DELIVERED"
extern const char* const ACK_SEEN;        // "SEEN"

// notify/push 를 해석한 결과. 문자열은 내용을 복사해 보관한다
// (수신 콜백의 버퍼는 콜백이 끝나면 사라지므로).
struct NotifyPush {
    char notify_id[40];
    char title[64];
    char text[128];
    char priority[8];      // low / normal / high / health
    char scenario[24];
    bool requires_ack;
    bool valid;            // false면 처리하지 않는다
};

// channels 배열에 my_virtual_id 가 들어 있는가
bool isForMe(const JsonDocument& doc, const char* my_virtual_id);

// 파싱 실패나 notify_id 부재 시 valid=false 로 돌려준다
NotifyPush parseNotifyPush(const JsonDocument& doc);

std::string buildNotifyAck(const char* src_id, uint32_t seq, uint32_t sent_ts,
                           const char* notify_id, const char* ack_type);


// ── 기기 명령 수신 ───────────────────────────
struct LightParams {
    bool     has_power;
    bool     has_brightness;
    bool     has_color_temp;

    char     power[12];        // "ON" / "OFF" / "STANDBY"
    uint8_t  brightness;       // 0~100
    uint16_t color_temp;       // 켈빈
};

struct DeviceCmd {
    bool        valid;
    char        cmd_id[32];
    char        device_type[24];
    char        action[12];
    LightParams light;         // device_type 이 smart_light 일 때만 의미
};

DeviceCmd parseDeviceCmd(const JsonDocument& doc);

// 토픽에서 virtual_id 추출. hestia/device/{vid}/cmd 형식이 아니면 false
bool parseDeviceCmdTopic(const char* topic, char* out, size_t out_size);

// ── 가전 state ───────────────────────────────
// brightness · color_temp 는 받은 적 있을 때만 싣는다.
// 변환값이 아니라 엔진에서 받은 값을 그대로 올려야 한다.
std::string buildSmartLightState(const char* src_id, uint32_t seq,
                                 uint32_t sent_ts,
                                 const char* power,
                                 bool has_brightness, uint8_t brightness,
                                 bool has_color_temp, uint16_t color_temp);


// ── 가전 이벤트 ──────────────────────────────
std::string buildDeviceEvent(const char* src_id, uint32_t seq, uint32_t sent_ts,
                             const char* device_type, const char* event_type);

std::string buildDispensed(const char* src_id, uint32_t seq, uint32_t sent_ts,
                           const char* water_type, uint16_t amount_ml);

std::string buildCooktopState(const char* src_id, uint32_t seq, uint32_t sent_ts,
                              bool cooking, uint8_t power_level, const char* zone);

// 닫을 때 실제로 열려 있던 시간을 싣는다. 고정값이면 '문을 오래 열어둔
// 이상' 을 판정할 방법이 없다.
std::string buildDoorClosed(const char* src_id, uint32_t seq, uint32_t sent_ts,
                            const char* device_type, float open_duration_sec);

std::string buildFridgeState(const char* src_id, uint32_t seq, uint32_t sent_ts,
                             bool open);
}  // namespace hestia

