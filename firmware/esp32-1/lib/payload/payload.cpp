#include "payload.h"
#include <cstring>

namespace hestia {

void putEnvelope(JsonDocument& doc,
                 const char* src_id,
                 uint32_t seq,
                 uint32_t sent_ts) {
    doc["version"] = SCHEMA_VERSION;
    doc["sent_ts"] = sent_ts;
    doc["src_id"]  = src_id;
    doc["seq"]     = seq;
}

const char* typeName(SensorType type){
    switch (type) {
        case SensorType::Presence: return "presence";
        case SensorType::Motion: return "motion";
        case SensorType::Door: return "door";
        case SensorType::Power: return "power";
        case SensorType::Bed: return "bed";
        case SensorType::Light: return "light";
        case SensorType::Climate: return "climate";
    }
    return "unknown";
}

bool parseSensorType(const char* name, SensorType& out) {
    if (name == nullptr) return false;

    if (strcmp(name, "presence") == 0) { out = SensorType::Presence; return true; }
    if (strcmp(name, "motion")   == 0) { out = SensorType::Motion;   return true; }
    if (strcmp(name, "door")     == 0) { out = SensorType::Door;     return true; }
    if (strcmp(name, "power")    == 0) { out = SensorType::Power;    return true; }
    if (strcmp(name, "bed")      == 0) { out = SensorType::Bed;      return true; }
    if (strcmp(name, "light")    == 0) { out = SensorType::Light;    return true; }
    if (strcmp(name, "climate")  == 0) { out = SensorType::Climate;  return true; }

    return false;
}

uint8_t qosFor(SensorType type) {
    switch (type) {
        case SensorType::Motion:
            return 0;
        default:
            return 1;
    }
}

bool isRetained(SensorType type) {
    switch (type) {
        case SensorType::Presence: return true;
        case SensorType::Motion: return false;
        case SensorType::Door: return true;
        case SensorType::Power: return true;
        case SensorType::Bed: return true;
        case SensorType::Light: return true;
        case SensorType::Climate: return true;
    }
    return true;
}

std::string sensorStateTopic(const char* virtual_id){
    std::string t = "hestia/sensor/";
    t += virtual_id;
    t += "/state";
    return t;
}

std::string deviceStateTopic(const char* virtual_id){
    std::string t = "hestia/device/";
    t += virtual_id;
    t += "/state";
    return t;
}

std::string notifyAckTopic(){
    return "hestia/notify/ack";
}

std::string buildPresence(const char* src_id, uint32_t seq, uint32_t sent_ts, 
    bool present, uint8_t energy, uint16_t distance_cm){
        JsonDocument doc;
        putEnvelope(doc, src_id, seq, sent_ts);

        doc["type"] = typeName(SensorType::Presence);
        doc["present"] = present;
        doc["confidence"] = "high";
        doc["energy"] = energy;
        doc["distance_cm"] = distance_cm;

        std::string out;
        serializeJson(doc, out);
        return out;    
    }

std::string buildMotion(const char* src_id, uint32_t seq, uint32_t sent_ts,
                        bool motion) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["type"] = typeName(SensorType::Motion);
    doc["motion"] = motion;
    doc["confidence"] = "low";

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string buildDoor(const char* src_id, uint32_t seq, uint32_t sent_ts,
                      bool open) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["type"] = typeName(SensorType::Door);
    doc["open"] = open;

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string buildPower(const char* src_id, uint32_t seq, uint32_t sent_ts,
                       uint16_t watt) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["type"] = typeName(SensorType::Power);
    doc["watt"] = watt;

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string buildBed(const char* src_id, uint32_t seq, uint32_t sent_ts,
                     bool occupied) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["type"] = typeName(SensorType::Bed);
    doc["occupied"] = occupied;

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string buildLight(const char* src_id, uint32_t seq, uint32_t sent_ts,
                       uint32_t illuminance_lux) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["type"] = typeName(SensorType::Light);
    doc["illuminance_lux"] = illuminance_lux;

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string buildClimate(const char* src_id, uint32_t seq, uint32_t sent_ts,
                         float temperature_c, uint8_t humidity_pct) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["type"] = typeName(SensorType::Climate);
    doc["temperature_c"] = temperature_c;
    doc["humidity_pct"] = humidity_pct;

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string buildDisplayState(const char* src_id, uint32_t seq, uint32_t sent_ts,
                              const char* display,
                              const char* notify_id) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["device_type"] = "display_node";
    doc["source"]      = "esp32";
    doc["power"]       = "ON";
    doc["display"]     = display;

    if (notify_id != nullptr && notify_id[0] != '\0') {
        doc["notify_id"] = notify_id;
    }

    std::string out;
    serializeJson(doc, out);
    return out;
}

//SeqCounter 클래스 함수
SeqCounter::SeqCounter() : count_(0) { }

bool SeqCounter::next(const char* virtual_id, uint32_t& out_seq) {
    // 이미 등록된 id인지
    for (uint8_t i = 0; i < count_; i++) {
        if (strcmp(slots_[i].id, virtual_id) == 0) { //strcmp는 두 문자열이 같으면 0 반환
            slots_[i].seq++;
            out_seq = slots_[i].seq;
            return true;
        }
    }
    // 처음 보는 id — 빈 슬롯에 등록
    if (count_ < MAX_IDS) {
        slots_[count_].id  = virtual_id; // count_가 곧 다음 빈 칸
        slots_[count_].seq = 0;
        out_seq = 0;
        count_++;
        return true;
    }
    // 슬롯 부족
    return false;
}

//노드(esp32보드) status 함수
std::string buildStatus(const char* node_id, uint32_t sent_ts,
                        bool online, bool ts_synced){
    JsonDocument doc;
    doc["version"] = SCHEMA_VERSION;
    doc["sent_ts"] = sent_ts;
    doc["src_id"]  = node_id;
    doc["online"]  = online;
    doc["ts_synced"] = ts_synced;

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string nodeStatusTopic(const char* node_id){
    std::string t = "hestia/node/";
    t += node_id;
    t += "/status";
    return t;
}

std::string nodeAnnounceTopic(const char* node_id){
    std::string t = "hestia/node/";
    t += node_id;
    t += "/announce";
    return t;
}


//노드(esp32) announce
Emulated sensorEmulated(const char* virtual_id, SensorType type) {
    return Emulated{ virtual_id, false, typeName(type) };
}

Emulated deviceEmulated(const char* virtual_id, const char* device_type) {
    return Emulated{ virtual_id, true, device_type };
}


//노드(esp32) announce
std::string buildAnnounce(const char* node_id, uint32_t seq, uint32_t sent_ts,
                          const char* fw,
                          const Emulated* items, uint8_t count){
    JsonDocument doc;
    putEnvelope(doc, node_id, seq, sent_ts);
    doc["fw"] = fw;
    JsonArray arr = doc["emulates"].to<JsonArray>();

    for (uint8_t i = 0; i < count; i++){
        JsonObject item = arr.add<JsonObject>();
        item["virtual_id"] = items[i].virtual_id;
        if (items[i].is_device) {
            item["device_type"] = items[i].type_name;
        } else {
            item["type"] = items[i].type_name;
        }
        item["source"] = "esp32";
    }

    std::string out;
    serializeJson(doc, out);
    return out;
}


// ── 알림 수신·확인 ───────────────────────────
const char* const ACK_DELIVERED = "DELIVERED";
const char* const ACK_SEEN      = "SEEN";

static void copyField(char* dst, size_t cap, const char* src) {
    if (src == nullptr) { dst[0] = '\0'; return; }
    strncpy(dst, src, cap - 1);
    dst[cap - 1] = '\0';
}

bool isForMe(const JsonDocument& doc, const char* my_virtual_id) {
    if (my_virtual_id == nullptr) return false;

    JsonArrayConst arr = doc["channels"].as<JsonArrayConst>();
    if (arr.isNull()) return false;

    for (JsonVariantConst v : arr) {
        const char* s = v.as<const char*>();
        if (s != nullptr && strcmp(s, my_virtual_id) == 0) {
            return true;
        }
    }
    return false;
}

NotifyPush parseNotifyPush(const JsonDocument& doc) {
    NotifyPush p{};              // 전 필드를 0으로
    p.valid = false;

    copyField(p.notify_id, sizeof(p.notify_id), doc["notify_id"] | "");
    if (p.notify_id[0] == '\0') {
        return p;                // ack·cancel 을 짝지을 수 없다
    }

    copyField(p.title,    sizeof(p.title),    doc["payload"]["title"] | "");
    copyField(p.text,     sizeof(p.text),     doc["payload"]["text"]  | "");
    copyField(p.priority, sizeof(p.priority), doc["priority"]         | "normal");
    copyField(p.scenario, sizeof(p.scenario), doc["scenario"]         | "");

    p.requires_ack = doc["requires_ack"] | false;
    p.valid = true;
    return p;
}

std::string buildNotifyAck(const char* src_id, uint32_t seq, uint32_t sent_ts,
                           const char* notify_id, const char* ack_type) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["notify_id"] = notify_id;
    doc["ack_type"]  = ack_type;

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string deviceCmdTopic(const char* virtual_id) {
    return std::string("hestia/device/") + virtual_id + "/cmd";
}

bool parseDeviceCmdTopic(const char* topic, char* out, size_t out_size) {
    static const char PREFIX[] = "hestia/device/";
    static const char SUFFIX[] = "/cmd";
    const size_t PRE_LEN = sizeof(PREFIX) - 1;
    const size_t SUF_LEN = sizeof(SUFFIX) - 1;

    if (topic == nullptr || out == nullptr || out_size == 0) return false;
    if (strncmp(topic, PREFIX, PRE_LEN) != 0) return false;

    size_t len = strlen(topic);
    if (len < PRE_LEN + SUF_LEN + 1) return false;          // vid 가 비었다
    if (strcmp(topic + len - SUF_LEN, SUFFIX) != 0) return false;

    size_t vid_len = len - PRE_LEN - SUF_LEN;
    if (vid_len >= out_size) return false;

    // vid 안에 '/' 가 있으면 토픽 레벨이 더 깊다는 뜻
    for (size_t i = 0; i < vid_len; i++) {
        if (topic[PRE_LEN + i] == '/') return false;
    }

    memcpy(out, topic + PRE_LEN, vid_len);
    out[vid_len] = '\0';
    return true;
}

static void copyStr(char* dst, size_t size, const char* src) {
    if (src == nullptr) { dst[0] = '\0'; return; }
    strncpy(dst, src, size - 1);
    dst[size - 1] = '\0';
}

DeviceCmd parseDeviceCmd(const JsonDocument& doc) {
    DeviceCmd c;
    memset(&c, 0, sizeof(c));

    const char* cmd_id = doc["cmd_id"]      | "";
    const char* dtype  = doc["device_type"] | "";
    const char* action = doc["action"]      | "";

    if (cmd_id[0] == '\0' || dtype[0] == '\0' || action[0] == '\0') {
        return c;                                   // valid = false
    }

    copyStr(c.cmd_id,      sizeof(c.cmd_id),      cmd_id);
    copyStr(c.device_type, sizeof(c.device_type), dtype);
    copyStr(c.action,      sizeof(c.action),      action);

    JsonVariantConst p = doc["params"];
    if (p.is<JsonObjectConst>()) {
        JsonVariantConst pw = p["power"];
        if (pw.is<const char*>()) {
            const char* s = pw.as<const char*>();
            if (s != nullptr && s[0] != '\0') {
                c.light.has_power = true;
                copyStr(c.light.power, sizeof(c.light.power), s);
            }
        }

        JsonVariantConst br = p["brightness"];
        if (br.is<int>()) {
            int v = br.as<int>();
            if (v < 0)   v = 0;
            if (v > 100) v = 100;
            c.light.has_brightness = true;
            c.light.brightness     = (uint8_t)v;
        }

        JsonVariantConst ct = p["color_temp"];
        if (ct.is<int>()) {
            int v = ct.as<int>();
            if (v < 0)     v = 0;
            if (v > 65535) v = 65535;
            c.light.has_color_temp = true;
            c.light.color_temp     = (uint16_t)v;
        }
    }

    c.valid = true;
    return c;
}

std::string buildSmartLightState(const char* src_id, uint32_t seq,
                                 uint32_t sent_ts,
                                 const char* power,
                                 bool has_brightness, uint8_t brightness,
                                 bool has_color_temp, uint16_t color_temp) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["device_type"] = "smart_light";
    doc["source"]      = "esp32";
    doc["power"]       = power;

    if (has_brightness) doc["brightness"] = brightness;
    if (has_color_temp) doc["color_temp"] = color_temp;

    std::string out;
    serializeJson(doc, out);
    return out;
}

}  // namespace hestia