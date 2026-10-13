#include <unity.h>
#include "payload.h"
#include "ld2410_frame.h"

using namespace hestia;

void setUp(void) {}
void tearDown(void) {}

// ── 환경 확인 ────────────────────────────────
void test_sanity(void) {
    TEST_ASSERT_EQUAL(2, 1 + 1);
}

void test_schema_version_defined(void) {
    TEST_ASSERT_EQUAL(1, SCHEMA_VERSION);
}

void test_type_name(void) {
    TEST_ASSERT_EQUAL_STRING("presence", typeName(SensorType::Presence));
    TEST_ASSERT_EQUAL_STRING("motion",   typeName(SensorType::Motion));
    TEST_ASSERT_EQUAL_STRING("door",     typeName(SensorType::Door));
    TEST_ASSERT_EQUAL_STRING("power",    typeName(SensorType::Power));
    TEST_ASSERT_EQUAL_STRING("bed",      typeName(SensorType::Bed));
    TEST_ASSERT_EQUAL_STRING("light",    typeName(SensorType::Light));
    TEST_ASSERT_EQUAL_STRING("climate",  typeName(SensorType::Climate));
}

// ── Envelope ─────────────────────────────────
void test_envelope_four_fields(void) {
    std::string json = buildDoor("vs-06", 88, 1755500000, true);

    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL(1, doc["version"].as<int>());
    TEST_ASSERT_EQUAL(1755500000, doc["sent_ts"].as<uint32_t>());
    TEST_ASSERT_EQUAL_STRING("vs-06", doc["src_id"].as<const char*>());
    TEST_ASSERT_EQUAL(88, doc["seq"].as<uint32_t>());
}

// ── QoS / retained 정책 ──────────────────────
void test_motion_policy(void) {
    TEST_ASSERT_EQUAL(0, qosFor(SensorType::Motion));
    TEST_ASSERT_FALSE(isRetained(SensorType::Motion));
}

void test_other_types_policy(void) {
    SensorType others[] = {
        SensorType::Presence, SensorType::Door, SensorType::Power,
        SensorType::Bed, SensorType::Light, SensorType::Climate
    };
    for (int i = 0; i < 6; i++) {
        TEST_ASSERT_EQUAL(1, qosFor(others[i]));
        TEST_ASSERT_TRUE(isRetained(others[i]));
    }
}

// ── 토픽 ─────────────────────────────────────
void test_topics(void) {
    TEST_ASSERT_EQUAL_STRING("hestia/sensor/vs-01/state",
                             sensorStateTopic("vs-01").c_str());
    TEST_ASSERT_EQUAL_STRING("hestia/node/esp32-ab34cd/status",
                             nodeStatusTopic("esp32-ab34cd").c_str());
    TEST_ASSERT_EQUAL_STRING("hestia/node/esp32-ab34cd/announce",
                             nodeAnnounceTopic("esp32-ab34cd").c_str());
    TEST_ASSERT_EQUAL_STRING("hestia/device/vd-10/state",
                             deviceStateTopic("vd-10").c_str());
    TEST_ASSERT_EQUAL_STRING("hestia/notify/ack",
                             notifyAckTopic().c_str());
}

// ── confidence 규칙 ──────────────────────────
void test_presence_confidence_high(void) {
    std::string json = buildPresence("vs-01", 4821, 1755500000, true, 42, 180);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL_STRING("presence", doc["type"].as<const char*>());
    TEST_ASSERT_TRUE(doc["present"].as<bool>());
    TEST_ASSERT_EQUAL_STRING("high", doc["confidence"].as<const char*>());
    TEST_ASSERT_EQUAL(42, doc["energy"].as<int>());
    TEST_ASSERT_EQUAL(180, doc["distance_cm"].as<int>());
}

void test_motion_confidence_low(void) {
    std::string json = buildMotion("vs-03", 112, 1755500000, true);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL_STRING("low", doc["confidence"].as<const char*>());
}

void test_no_confidence_on_deterministic_types(void) {
    JsonDocument doc;

    deserializeJson(doc, buildDoor("vs-06", 1, 100, true));
    TEST_ASSERT_TRUE(doc["confidence"].isNull());

    deserializeJson(doc, buildPower("vs-08", 1, 100, 1180));
    TEST_ASSERT_TRUE(doc["confidence"].isNull());

    deserializeJson(doc, buildBed("vs-07", 1, 100, true));
    TEST_ASSERT_TRUE(doc["confidence"].isNull());

    deserializeJson(doc, buildLight("vs-05l", 1, 100, 12));
    TEST_ASSERT_TRUE(doc["confidence"].isNull());

    deserializeJson(doc, buildClimate("vs-05c", 1, 100, 24.3f, 48));
    TEST_ASSERT_TRUE(doc["confidence"].isNull());
}

// ── 나머지 센서 필드 ─────────────────────────
void test_climate_fields(void) {
    std::string json = buildClimate("vs-05c", 56, 1755500000, 24.3f, 48);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL_FLOAT(24.3f, doc["temperature_c"].as<float>());
    TEST_ASSERT_EQUAL(48, doc["humidity_pct"].as<int>());
}

void test_power_has_no_state(void) {
    std::string json = buildPower("vs-08", 204, 1755500000, 1180);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL(1180, doc["watt"].as<int>());
    TEST_ASSERT_TRUE(doc["state"].isNull());
}

// ── seq 카운터 ───────────────────────────────
void test_seq_starts_at_zero_and_increments(void) {
    SeqCounter c;
    uint32_t s;

    TEST_ASSERT_TRUE(c.next("vs-01", s));
    TEST_ASSERT_EQUAL(0, s);
    TEST_ASSERT_TRUE(c.next("vs-01", s));
    TEST_ASSERT_EQUAL(1, s);
    TEST_ASSERT_TRUE(c.next("vs-01", s));
    TEST_ASSERT_EQUAL(2, s);
}

void test_seq_independent_per_virtual_id(void) {
    SeqCounter c;
    uint32_t s;

    c.next("vs-01", s);              // 0
    c.next("vs-01", s);              // 1
    c.next("vs-03", s);
    TEST_ASSERT_EQUAL(0, s);         // vs-03은 독립적으로 0부터

    c.next("vs-01", s);
    TEST_ASSERT_EQUAL(2, s);         // vs-01은 이어서 2
}

void test_seq_slots_full(void) {
    SeqCounter c;
    uint32_t s;
    const char* ids[] = {"a","b","c","d","e","f","g","h"};

    for (int i = 0; i < 8; i++) {
        TEST_ASSERT_TRUE(c.next(ids[i], s));
    }
    TEST_ASSERT_FALSE(c.next("overflow", s));   // 9번째는 실패
}

// ── status ───────────────────────────────────
void test_status_has_no_seq(void) {
    std::string json = buildStatus("esp32-ab34cd", 1755500000, true, true);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL_STRING("esp32-ab34cd", doc["src_id"].as<const char*>());
    TEST_ASSERT_TRUE(doc["online"].as<bool>());
    TEST_ASSERT_TRUE(doc["ts_synced"].as<bool>());
    TEST_ASSERT_TRUE(doc["seq"].isNull());
}

void test_status_lwt_form(void) {
    std::string json = buildStatus("esp32-ab34cd", 0, false, false);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_FALSE(doc["online"].as<bool>());
    TEST_ASSERT_FALSE(doc["ts_synced"].as<bool>());
}

// ── announce ─────────────────────────────────
void test_announce_emulates(void) {
    Emulated items[] = {
        sensorEmulated("vs-01", SensorType::Presence),
        deviceEmulated("vd-10", "display_node")
    };
    std::string json = buildAnnounce("esp32-ab34cd", 1, 1755500000, "0.1.0", items, 2);

    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL(1, doc["seq"].as<uint32_t>());
    TEST_ASSERT_EQUAL_STRING("0.1.0", doc["fw"].as<const char*>());

    JsonArray arr = doc["emulates"].as<JsonArray>();
    TEST_ASSERT_EQUAL(2, arr.size());

    TEST_ASSERT_EQUAL_STRING("vs-01", arr[0]["virtual_id"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("presence", arr[0]["type"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("esp32", arr[0]["source"].as<const char*>());
    TEST_ASSERT_TRUE(arr[0]["device_type"].isNull());

    TEST_ASSERT_EQUAL_STRING("vd-10", arr[1]["virtual_id"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("display_node", arr[1]["device_type"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("esp32", arr[1]["source"].as<const char*>());
    TEST_ASSERT_TRUE(arr[1]["type"].isNull());
}

void test_announce_empty_list(void) {
    std::string json = buildAnnounce("esp32-ab34cd", 1, 1755500000, "0.1.0", nullptr, 0);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL(0, doc["emulates"].as<JsonArray>().size());
}


// ── display_node state ───────────────────────
void test_display_state_empty(void) {
    std::string json = buildDisplayState("vd-10", 7, 1791209970, "", nullptr);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL_STRING("vd-10", doc["src_id"].as<const char*>());
    TEST_ASSERT_EQUAL(7, doc["seq"].as<uint32_t>());
    TEST_ASSERT_EQUAL_STRING("display_node", doc["device_type"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("esp32", doc["source"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("ON", doc["power"].as<const char*>());

    TEST_ASSERT_EQUAL_STRING("", doc["display"].as<const char*>());
    TEST_ASSERT_FALSE(doc["display"].isNull());    // 키는 있어야 한다
    TEST_ASSERT_TRUE(doc["notify_id"].isNull());   // 키가 없어야 한다
}

void test_display_state_showing(void) {
    std::string json = buildDisplayState("vd-10", 8, 1791209970,
                                         "물 한 잔 드세요", "n-20261005-001");
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL_STRING("물 한 잔 드세요", doc["display"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("n-20261005-001", doc["notify_id"].as<const char*>());
}

void test_display_state_blank_notify_id_omitted(void) {
    std::string json = buildDisplayState("vd-10", 9, 1791209970, "", "");
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_TRUE(doc["notify_id"].isNull());   // "" 도 걸러낸다
}


// ── 알림 수신 ────────────────────────────────
static void loadPush(JsonDocument& doc, const char* json) {
    deserializeJson(doc, json);
}

void test_is_for_me(void) {
    JsonDocument doc;

    loadPush(doc, R"({"channels":["vd-01","vd-10"]})");
    TEST_ASSERT_TRUE(isForMe(doc, "vd-10"));

    loadPush(doc, R"({"channels":["vd-01"]})");
    TEST_ASSERT_FALSE(isForMe(doc, "vd-10"));

    loadPush(doc, R"({"channels":["voice"]})");
    TEST_ASSERT_FALSE(isForMe(doc, "vd-10"));

    loadPush(doc, R"({"channels":[]})");
    TEST_ASSERT_FALSE(isForMe(doc, "vd-10"));

    loadPush(doc, R"({})");                       // channels 자체가 없음
    TEST_ASSERT_FALSE(isForMe(doc, "vd-10"));
}

void test_parse_push_full(void) {
    JsonDocument doc;
    loadPush(doc, R"({"version":1,"sent_ts":1791197088,"src_id":"rpi5",
        "notify_id":"n-20261005-001","scenario":"WAKE_ROUTINE",
        "priority":"normal","channels":["vd-10"],
        "requires_ack":true,"ack_deadline":1791197688,"escalation_level":1,
        "payload":{"title":"수분 섭취","text":"물 한 잔 드세요"}})");

    NotifyPush p = parseNotifyPush(doc);

    TEST_ASSERT_TRUE(p.valid);
    TEST_ASSERT_EQUAL_STRING("n-20261005-001", p.notify_id);
    TEST_ASSERT_EQUAL_STRING("수분 섭취", p.title);
    TEST_ASSERT_EQUAL_STRING("물 한 잔 드세요", p.text);
    TEST_ASSERT_EQUAL_STRING("normal", p.priority);
    TEST_ASSERT_EQUAL_STRING("WAKE_ROUTINE", p.scenario);
    TEST_ASSERT_TRUE(p.requires_ack);
}

void test_parse_push_missing_fields(void) {
    JsonDocument doc;

    // notify_id 만 있는 최소 페이로드
    loadPush(doc, R"({"notify_id":"n-001"})");
    NotifyPush p = parseNotifyPush(doc);
    TEST_ASSERT_TRUE(p.valid);
    TEST_ASSERT_EQUAL_STRING("", p.title);
    TEST_ASSERT_EQUAL_STRING("", p.text);
    TEST_ASSERT_EQUAL_STRING("normal", p.priority);   // 기본값
    TEST_ASSERT_FALSE(p.requires_ack);

    // notify_id 없음 → 처리 불가
    loadPush(doc, R"({"priority":"high"})");
    TEST_ASSERT_FALSE(parseNotifyPush(doc).valid);

    // 타입이 다름
    loadPush(doc, R"({"notify_id":12345,"requires_ack":"yes"})");
    TEST_ASSERT_FALSE(parseNotifyPush(doc).valid);    // 숫자는 문자열로 못 읽음
}

void test_notify_ack(void) {
    std::string json = buildNotifyAck("vd-10", 42, 1791197100,
                                      "n-20261005-001", ACK_DELIVERED);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL_STRING("vd-10", doc["src_id"].as<const char*>());
    TEST_ASSERT_EQUAL(42, doc["seq"].as<uint32_t>());
    TEST_ASSERT_EQUAL_STRING("n-20261005-001", doc["notify_id"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("DELIVERED", doc["ack_type"].as<const char*>());

    json = buildNotifyAck("vd-10", 43, 1791197200, "n-20261005-001", ACK_SEEN);
    deserializeJson(doc, json);
    TEST_ASSERT_EQUAL_STRING("SEEN", doc["ack_type"].as<const char*>());
}


void test_parse_sensor_type(void) {
    SensorType t;
    TEST_ASSERT_TRUE(parseSensorType("motion", t));
    TEST_ASSERT_EQUAL(SensorType::Motion, t);

    TEST_ASSERT_TRUE(parseSensorType("climate", t));
    TEST_ASSERT_EQUAL(SensorType::Climate, t);

    TEST_ASSERT_FALSE(parseSensorType("bogus", t));
    TEST_ASSERT_FALSE(parseSensorType(nullptr, t));
}

// typeName 과 parseSensorType 이 왕복하는가
void test_sensor_type_roundtrip(void) {
    SensorType all[] = {
        SensorType::Presence, SensorType::Motion, SensorType::Door,
        SensorType::Power, SensorType::Bed, SensorType::Light, SensorType::Climate
    };
    for (int i = 0; i < 7; i++) {
        SensorType back;
        TEST_ASSERT_TRUE(parseSensorType(typeName(all[i]), back));
        TEST_ASSERT_EQUAL(all[i], back);
    }
}


// ── LD2410 파서 ──────────────────────────────
using hestia::Ld2410Parser;
using hestia::Ld2410Frame;

// 정상 프레임 한 벌 (state=2 정지, detect=137cm)
static const uint8_t GOOD[] = {
    0xF4, 0xF3, 0xF2, 0xF1,
    0x0D, 0x00,                          // len = 13
    0x02, 0xAA,
    0x02,                                // state = 정지
    0x32, 0x00,                          // moving_dist = 50
    0x14,                                // moving_energy = 20
    0x89, 0x00,                          // static_dist = 137
    0x3C,                                // static_energy = 60
    0x89, 0x00,                          // detect_dist = 137
    0x55, 0x00,
    0xF8, 0xF7, 0xF6, 0xF5
};

static bool feedAll(Ld2410Parser& p, const uint8_t* buf, size_t n,
                    Ld2410Frame& out) {
    bool got = false;
    for (size_t i = 0; i < n; i++) {
        if (p.feed(buf[i], out)) got = true;
    }
    return got;
}

void test_ld_good_frame(void) {
    Ld2410Parser p;
    Ld2410Frame  f;

    TEST_ASSERT_TRUE(feedAll(p, GOOD, sizeof(GOOD), f));
    TEST_ASSERT_EQUAL(2,   f.state);
    TEST_ASSERT_EQUAL(50,  f.moving_dist_cm);
    TEST_ASSERT_EQUAL(20,  f.moving_energy);
    TEST_ASSERT_EQUAL(137, f.static_dist_cm);
    TEST_ASSERT_EQUAL(60,  f.static_energy);
    TEST_ASSERT_EQUAL(137, f.detect_dist_cm);
    TEST_ASSERT_EQUAL(1,   p.frameCount());
    TEST_ASSERT_EQUAL(0,   p.errorCount());
}

void test_ld_two_frames_back_to_back(void) {
    Ld2410Parser p;
    Ld2410Frame  f;

    feedAll(p, GOOD, sizeof(GOOD), f);
    TEST_ASSERT_TRUE(feedAll(p, GOOD, sizeof(GOOD), f));
    TEST_ASSERT_EQUAL(2, p.frameCount());
}

// 앞에 쓰레기 바이트가 붙어도 헤더를 찾아낸다
void test_ld_leading_garbage(void) {
    Ld2410Parser p;
    Ld2410Frame  f;

    uint8_t junk[] = { 0x00, 0xFF, 0x12, 0xF4, 0x99 };
    feedAll(p, junk, sizeof(junk), f);

    TEST_ASSERT_TRUE(feedAll(p, GOOD, sizeof(GOOD), f));
    TEST_ASSERT_EQUAL(137, f.detect_dist_cm);
}

// F4 가 연속으로 와도 뒤쪽 헤더를 잡는다
void test_ld_repeated_f4(void) {
    Ld2410Parser p;
    Ld2410Frame  f;

    uint8_t pre[] = { 0xF4, 0xF4, 0xF3, 0xF4 };
    feedAll(p, pre, sizeof(pre), f);

    TEST_ASSERT_TRUE(feedAll(p, GOOD, sizeof(GOOD), f));
    TEST_ASSERT_EQUAL(1, p.frameCount());
}

// 꼬리가 틀리면 버린다
void test_ld_bad_tail(void) {
    Ld2410Parser p;
    Ld2410Frame  f;

    uint8_t bad[sizeof(GOOD)];
    memcpy(bad, GOOD, sizeof(GOOD));
    bad[sizeof(GOOD) - 1] = 0x00;        // F5 -> 00

    TEST_ASSERT_FALSE(feedAll(p, bad, sizeof(bad), f));
    TEST_ASSERT_EQUAL(0, p.frameCount());
    TEST_ASSERT_EQUAL(1, p.errorCount());
}

// 길이가 말이 안 되면 버퍼를 안 넘긴다
void test_ld_absurd_length(void) {
    Ld2410Parser p;
    Ld2410Frame  f;

    uint8_t bad[] = {
        0xF4, 0xF3, 0xF2, 0xF1,
        0xFF, 0xFF,                      // len = 65535
        0x02, 0xAA
    };
    TEST_ASSERT_FALSE(feedAll(p, bad, sizeof(bad), f));
    TEST_ASSERT_EQUAL(1, p.errorCount());
}

// 잘린 프레임 뒤에 정상 프레임이 와도 복구된다
void test_ld_truncated_then_good(void) {
    Ld2410Parser p;
    Ld2410Frame  f;

    feedAll(p, GOOD, 10, f);             // 잘린 프레임
    feedAll(p, GOOD, sizeof(GOOD), f);   // 이건 희생된다

    TEST_ASSERT_TRUE(feedAll(p, GOOD, sizeof(GOOD), f));
    TEST_ASSERT_EQUAL(1, p.frameCount());
}

// 기본 모드가 아니면(02 AA 아님) 버린다
void test_ld_wrong_mode(void) {
    Ld2410Parser p;
    Ld2410Frame  f;

    uint8_t bad[sizeof(GOOD)];
    memcpy(bad, GOOD, sizeof(GOOD));
    bad[6] = 0x01;                       // 02 -> 01 (engineering mode)

    TEST_ASSERT_FALSE(feedAll(p, bad, sizeof(bad), f));
    TEST_ASSERT_EQUAL(1, p.errorCount());
}

void test_ld_no_presence(void) {
    Ld2410Parser p;
    Ld2410Frame  f;

    uint8_t none[sizeof(GOOD)];
    memcpy(none, GOOD, sizeof(GOOD));
    none[8]  = 0x00;                     // state = 0
    none[15] = 0x00;                     // detect_dist = 0
    none[16] = 0x00;

    TEST_ASSERT_TRUE(feedAll(p, none, sizeof(none), f));
    TEST_ASSERT_EQUAL(0, f.state);
    TEST_ASSERT_EQUAL(0, f.detect_dist_cm);
}

// ── 기기 명령 ────────────────────────────────
void test_cmd_topic_parse(void) {
    char vid[24];

    TEST_ASSERT_TRUE(parseDeviceCmdTopic("hestia/device/vd-02/cmd",
                                         vid, sizeof(vid)));
    TEST_ASSERT_EQUAL_STRING("vd-02", vid);
}

void test_cmd_topic_reject(void) {
    char vid[24];

    TEST_ASSERT_FALSE(parseDeviceCmdTopic("hestia/device/vd-02/state",
                                          vid, sizeof(vid)));
    TEST_ASSERT_FALSE(parseDeviceCmdTopic("hestia/sensor/vs-01/cmd",
                                          vid, sizeof(vid)));
    TEST_ASSERT_FALSE(parseDeviceCmdTopic("hestia/device//cmd",
                                          vid, sizeof(vid)));
    TEST_ASSERT_FALSE(parseDeviceCmdTopic("hestia/device/a/b/cmd",
                                          vid, sizeof(vid)));
}

void test_cmd_topic_buffer_too_small(void) {
    char vid[4];
    TEST_ASSERT_FALSE(parseDeviceCmdTopic("hestia/device/vd-02/cmd",
                                          vid, sizeof(vid)));
}

void test_parse_cmd_full(void) {
    const char* json =
        "{\"version\":1,\"src_id\":\"rpi5\",\"sent_ts\":1760400000,"
        "\"cmd_id\":\"c-7f3a\",\"device_type\":\"smart_light\","
        "\"action\":\"set\",\"params\":{\"power\":\"ON\","
        "\"brightness\":15,\"color_temp\":2700},"
        "\"reason\":\"NIGHT_LIGHT\"}";

    JsonDocument doc;
    deserializeJson(doc, json);
    DeviceCmd c = parseDeviceCmd(doc);

    TEST_ASSERT_TRUE(c.valid);
    TEST_ASSERT_EQUAL_STRING("c-7f3a", c.cmd_id);
    TEST_ASSERT_EQUAL_STRING("smart_light", c.device_type);
    TEST_ASSERT_EQUAL_STRING("set", c.action);
    TEST_ASSERT_TRUE(c.light.has_power);
    TEST_ASSERT_EQUAL_STRING("ON", c.light.power);
    TEST_ASSERT_TRUE(c.light.has_brightness);
    TEST_ASSERT_EQUAL(15, c.light.brightness);
    TEST_ASSERT_TRUE(c.light.has_color_temp);
    TEST_ASSERT_EQUAL(2700, c.light.color_temp);
}

// params 에 일부만 오면 나머지는 has_* 가 false
void test_parse_cmd_partial_params(void) {
    const char* json =
        "{\"version\":1,\"cmd_id\":\"c-1\",\"device_type\":\"smart_light\","
        "\"action\":\"set\",\"params\":{\"brightness\":30}}";

    JsonDocument doc;
    deserializeJson(doc, json);
    DeviceCmd c = parseDeviceCmd(doc);

    TEST_ASSERT_TRUE(c.valid);
    TEST_ASSERT_FALSE(c.light.has_power);
    TEST_ASSERT_TRUE(c.light.has_brightness);
    TEST_ASSERT_EQUAL(30, c.light.brightness);
    TEST_ASSERT_FALSE(c.light.has_color_temp);
}

void test_parse_cmd_no_params(void) {
    const char* json =
        "{\"cmd_id\":\"c-1\",\"device_type\":\"smart_light\","
        "\"action\":\"set\"}";

    JsonDocument doc;
    deserializeJson(doc, json);
    DeviceCmd c = parseDeviceCmd(doc);

    TEST_ASSERT_TRUE(c.valid);
    TEST_ASSERT_FALSE(c.light.has_power);
    TEST_ASSERT_FALSE(c.light.has_brightness);
}

void test_parse_cmd_missing_required(void) {
    const char* json = "{\"device_type\":\"smart_light\",\"action\":\"set\"}";

    JsonDocument doc;
    deserializeJson(doc, json);
    DeviceCmd c = parseDeviceCmd(doc);

    TEST_ASSERT_FALSE(c.valid);          // cmd_id 없음
}

// 모르는 필드가 있어도 파싱은 통과한다
void test_parse_cmd_unknown_fields(void) {
    const char* json =
        "{\"cmd_id\":\"c-1\",\"device_type\":\"smart_light\","
        "\"action\":\"set\",\"params\":{\"power\":\"ON\",\"hue\":120},"
        "\"future_field\":{\"a\":1}}";

    JsonDocument doc;
    deserializeJson(doc, json);
    DeviceCmd c = parseDeviceCmd(doc);

    TEST_ASSERT_TRUE(c.valid);
    TEST_ASSERT_TRUE(c.light.has_power);
}

// 타입이 틀리면 없는 것으로 본다
void test_parse_cmd_wrong_types(void) {
    const char* json =
        "{\"cmd_id\":\"c-1\",\"device_type\":\"smart_light\","
        "\"action\":\"set\",\"params\":{\"brightness\":\"30\","
        "\"power\":123}}";

    JsonDocument doc;
    deserializeJson(doc, json);
    DeviceCmd c = parseDeviceCmd(doc);

    TEST_ASSERT_TRUE(c.valid);
    TEST_ASSERT_FALSE(c.light.has_brightness);
    TEST_ASSERT_FALSE(c.light.has_power);
}

void test_parse_cmd_brightness_clamped(void) {
    const char* json =
        "{\"cmd_id\":\"c-1\",\"device_type\":\"smart_light\","
        "\"action\":\"set\",\"params\":{\"brightness\":150}}";

    JsonDocument doc;
    deserializeJson(doc, json);
    DeviceCmd c = parseDeviceCmd(doc);

    TEST_ASSERT_EQUAL(100, c.light.brightness);
}

// ── smart_light state ────────────────────────
void test_light_state_full(void) {
    std::string json = buildSmartLightState("vd-02", 3, 1760400001,
                                            "ON", true, 15, true, 2700);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL(1, doc["version"].as<int>());
    TEST_ASSERT_EQUAL_STRING("vd-02", doc["src_id"].as<const char*>());
    TEST_ASSERT_EQUAL(3, doc["seq"].as<uint32_t>());
    TEST_ASSERT_EQUAL_STRING("smart_light",
                             doc["device_type"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("esp32", doc["source"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("ON", doc["power"].as<const char*>());
    TEST_ASSERT_EQUAL(15,   doc["brightness"].as<int>());
    TEST_ASSERT_EQUAL(2700, doc["color_temp"].as<int>());
}

// 받은 적 없는 값은 아예 빠진다
void test_light_state_omits_unset(void) {
    std::string json = buildSmartLightState("vd-02", 0, 1760400001,
                                            "OFF", false, 0, false, 0);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_TRUE(doc["brightness"].isNull());
    TEST_ASSERT_TRUE(doc["color_temp"].isNull());
    TEST_ASSERT_EQUAL_STRING("OFF", doc["power"].as<const char*>());
}

// brightness 0 은 "없음" 이 아니라 값이다
void test_light_state_brightness_zero_kept(void) {
    std::string json = buildSmartLightState("vd-02", 0, 1760400001,
                                            "ON", true, 0, false, 0);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_FALSE(doc["brightness"].isNull());
    TEST_ASSERT_EQUAL(0, doc["brightness"].as<int>());
}



int main(int argc, char** argv) {
    UNITY_BEGIN();

    RUN_TEST(test_sanity);
    RUN_TEST(test_schema_version_defined);
    RUN_TEST(test_type_name);

    RUN_TEST(test_envelope_four_fields);
    RUN_TEST(test_motion_policy);
    RUN_TEST(test_other_types_policy);
    RUN_TEST(test_topics);

    RUN_TEST(test_presence_confidence_high);
    RUN_TEST(test_motion_confidence_low);
    RUN_TEST(test_no_confidence_on_deterministic_types);
    RUN_TEST(test_climate_fields);
    RUN_TEST(test_power_has_no_state);

    RUN_TEST(test_seq_starts_at_zero_and_increments);
    RUN_TEST(test_seq_independent_per_virtual_id);
    RUN_TEST(test_seq_slots_full);

    RUN_TEST(test_status_has_no_seq);
    RUN_TEST(test_status_lwt_form);

    RUN_TEST(test_announce_emulates);
    RUN_TEST(test_announce_empty_list);

    RUN_TEST(test_display_state_empty);
    RUN_TEST(test_display_state_showing);
    RUN_TEST(test_display_state_blank_notify_id_omitted);

    RUN_TEST(test_is_for_me);
    RUN_TEST(test_parse_push_full);
    RUN_TEST(test_parse_push_missing_fields);
    RUN_TEST(test_notify_ack);

    RUN_TEST(test_parse_sensor_type);
    RUN_TEST(test_sensor_type_roundtrip);

    RUN_TEST(test_ld_good_frame);
    RUN_TEST(test_ld_two_frames_back_to_back);
    RUN_TEST(test_ld_leading_garbage);
    RUN_TEST(test_ld_repeated_f4);
    RUN_TEST(test_ld_bad_tail);
    RUN_TEST(test_ld_absurd_length);
    RUN_TEST(test_ld_truncated_then_good);
    RUN_TEST(test_ld_wrong_mode);
    RUN_TEST(test_ld_no_presence);

    RUN_TEST(test_cmd_topic_parse);
    RUN_TEST(test_cmd_topic_reject);
    RUN_TEST(test_cmd_topic_buffer_too_small);
    RUN_TEST(test_parse_cmd_full);
    RUN_TEST(test_parse_cmd_partial_params);
    RUN_TEST(test_parse_cmd_no_params);
    RUN_TEST(test_parse_cmd_missing_required);
    RUN_TEST(test_parse_cmd_unknown_fields);
    RUN_TEST(test_parse_cmd_wrong_types);
    RUN_TEST(test_parse_cmd_brightness_clamped);
    RUN_TEST(test_light_state_full);
    RUN_TEST(test_light_state_omits_unset);
    RUN_TEST(test_light_state_brightness_zero_kept);

    UNITY_END();
    return 0;
}