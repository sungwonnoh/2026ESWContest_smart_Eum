#include <Arduino.h>
#include "logging.h"
#include "wifi_manager.h"
#include "mqtt_node.h"
#include "time_sync.h"
#include "node_id.h"
#include "payload.h"
#include "display.h"
#include "notify_store.h"
#include "console.h"
#include "secrets.h"
#include "inventory.h"      
#include "sensors.h"
#include "devices.h"
#include "sensors/button.h"

static const char* displayId_ = nullptr;         // setup 에서 결정

// 시연용 시계. 도메인 메시지가 아니라 제어 신호다.
static const char* SIM_CLOCK_TOPIC = "hestia/sim/clock";
static bool clockJumped_ = false;                // loop 에서 재발행하라는 표시

static WifiManager wifi(WIFI_SSID, WIFI_PASSWORD);
static MqttNode    mqtt(wifi, MQTT_HOST, MQTT_PORT);
static NotifyStore notifies;
static hestia::SeqCounter seqs;

static bool     wasWifiUp     = false;
static bool     wasMqttUp     = false;
static uint32_t lastHeartbeat = 0;
static uint32_t lastDeviceState = 0;

static const uint32_t DEVICE_STATE_MS = 300000;   // 5분

static ButtonPin ackBtn_;

// ── 발행 ─────────────────────────────────────
static void publishStatus() {
    std::string topic   = hestia::nodeStatusTopic(nodeId());
    std::string payload = hestia::buildStatus(nodeId(), nowTs(),
                                              true, isTimeSynced());

    if (mqtt.publish(topic.c_str(), 1, true, payload.c_str())) {
        logInfo("STAT", "%s", payload.c_str());
    } else {
        logWarn("STAT", "publish failed");
    }
    lastHeartbeat = millis();
}

static void publishAnnounce() {
    hestia::Emulated em[12];
    uint8_t n = buildEmulates(em, 12);

    uint32_t seq = 0;
    if (!seqs.next(nodeId(), seq)) {
        logError("ANN", "seq slots full");
        return;
    }

    std::string topic   = hestia::nodeAnnounceTopic(nodeId());
    std::string payload = hestia::buildAnnounce(nodeId(), seq, nowTs(),
                                                FW_VERSION, em, n);
    if (mqtt.publish(topic.c_str(), 1, true, payload.c_str())) {
        logInfo("ANN", "%s", payload.c_str());
    }
}

static void publishDeviceState() {
    if (displayId_ == nullptr) return;

    const hestia::NotifyPush* top = notifies.latest();
    const char* nid = (top != nullptr) ? top->notify_id : nullptr;

    uint32_t seq = 0;
    if (!seqs.next(displayId_, seq)) {
        logError("DEV", "seq slots full");
        return;
    }

    std::string topic = hestia::deviceStateTopic(displayId_);
    std::string payload = hestia::buildDisplayState(
        displayId_, seq, nowTs(), displayCurrent(), nid);

    if (mqtt.publish(topic.c_str(), 1, true, payload.c_str())) {
        logInfo("DEV", "%s", payload.c_str());
    } else {
        logWarn("DEV", "publish failed");
    }
    lastDeviceState = millis();
}

static void publishAck(const char* notify_id, const char* ack_type) {
    if (displayId_ == nullptr) return; 

    uint32_t seq = 0;
    if (!seqs.next(displayId_, seq)) {
        logError("NOTI", "seq slots full");
        return;
    }

    std::string topic   = hestia::notifyAckTopic();
    std::string payload = hestia::buildNotifyAck(displayId_, seq, nowTs(),
                                                 notify_id, ack_type);
    if (mqtt.publish(topic.c_str(), 1, false, payload.c_str())) {
        logInfo("NOTI", "ack %s %s", ack_type, notify_id);
    } else {
        logWarn("NOTI", "ack publish failed");
    }
}

// 발행 함수를 sensors 에 넘기기 위한 래퍼
static bool mqttPublishFn(const char* topic, uint8_t qos,
                          bool retain, const char* payload) {
    return mqtt.publish(topic, qos, retain, payload);
}


// ── 수신 ─────────────────────────────────────
static void onNotifyPush(const char* payload, size_t len) {
    if (displayId_ == nullptr) return; 

    JsonDocument doc;
    if (deserializeJson(doc, payload, len) != DeserializationError::Ok) {
        logWarn("NOTI", "push parse failed");
        return;
    }
    if (!hestia::isForMe(doc, displayId_)) {
        return;                              // 남의 알림
    }

    hestia::NotifyPush p = hestia::parseNotifyPush(doc);
    if (!p.valid) {
        logWarn("NOTI", "push missing notify_id");
        return;
    }

    notifies.add(p, millis());
    displayShow(p.title, p.text, p.priority);
    publishAck(p.notify_id, hestia::ACK_DELIVERED);
    publishDeviceState();
}

static void onNotifyCancel(const char* payload, size_t len) {
    JsonDocument doc;
    if (deserializeJson(doc, payload, len) != DeserializationError::Ok) {
        return;
    }
    const char* nid = doc["notify_id"] | "";
    if (nid[0] == '\0') return;

    if (!notifies.remove(nid)) {
        return;                              // 내가 안 들고 있던 알림
    }
    logInfo("NOTI", "cancel %s", nid);

    const hestia::NotifyPush* top = notifies.latest();
    if (top != nullptr) {
        displayShow(top->title, top->text, top->priority);
    } else {
        displayClear();
    }
    publishDeviceState();
}

static void onDeviceCmd(const char* topic, const char* payload, size_t len) {
    char vid[24];
    if (!hestia::parseDeviceCmdTopic(topic, vid, sizeof(vid))) {
        return;                              // 형식이 아니다
    }

    JsonDocument doc;
    if (deserializeJson(doc, payload, len) != DeserializationError::Ok) {
        logWarn("DEV", "cmd parse failed for %s", vid);
        return;
    }

    hestia::DeviceCmd cmd = hestia::parseDeviceCmd(doc);
    if (!cmd.valid) {
        logWarn("DEV", "cmd missing required fields for %s", vid);
        return;
    }

    logInfo("DEV", "cmd %s -> %s action=%s", cmd.cmd_id, vid, cmd.action);
    devicesHandleCmd(vid, cmd);
}

static void onSimClock(const char* payload, size_t len) {
    JsonDocument doc;
    if (deserializeJson(doc, payload, len) != DeserializationError::Ok) {
        logWarn("CLOCK", "parse failed");
        return;
    }
    int32_t off = (int32_t)(double)(doc["offset"] | 0.0);
    if (off == timeOffset()) {
        return;                      // retained 재수신. 이미 그 시각이다
    }
    timeSetOffset(off);
    clockJumped_ = true;
    logInfo("CLOCK", "offset=%ds ts=%lu", (int)off, (unsigned long)nowTs());
}

static void onMessage(const char* topic, const char* payload, size_t len) {
    if (strcmp(topic, SIM_CLOCK_TOPIC) == 0) {
        onSimClock(payload, len);
    } else if (strcmp(topic, "hestia/notify/push") == 0) {
        onNotifyPush(payload, len);
    } else if (strcmp(topic, "hestia/notify/cancel") == 0) {
        onNotifyCancel(payload, len);
    } else if (strncmp(topic, "hestia/device/", 14) == 0) {
        onDeviceCmd(topic, payload, len);
    } else {
        logInfo("MQTT", "rx %s (%u bytes)", topic, (unsigned)len);
    }
}

// ── 콘솔 ─────────────────────────────────────
static void onConsoleLine(const char* line) {
    if (strcmp(line, "list") == 0) {
        logInfo("CON", "active=%u display=\"%s\"",
                notifies.count(), displayCurrent());
        for (uint8_t i = 0; i < NotifyStore::MAX_ACTIVE; i++) {
            const hestia::NotifyPush* p = notifies.at(i);
            if (p != nullptr) {
                logInfo("CON", "  [%u] %s %s", i, p->notify_id, p->text);
            }
        }
        return;
    }

    if (strncmp(line, "seen", 4) == 0) {
        const char* nid = nullptr;
        if (line[4] == ' ') {
            nid = line + 5;                  // "seen n-001"
        } else {
            const hestia::NotifyPush* top = notifies.latest();
            if (top == nullptr) {
                logWarn("CON", "no active notify");
                return;
            }
            nid = top->notify_id;
        }
        publishAck(nid, hestia::ACK_SEEN);
        return;
    }
    if (strncmp(line, "adc ", 4) == 0) {
        sensorsAdcDebug(strcmp(line + 4, "on") == 0);
        return;
    }

    if (strcmp(line, "cal") == 0) {
        sensorsCalibrate();
        return;
    }

    if (strcmp(line, "stats") == 0) {
        sensorsStats();
        return;
    }

    logWarn("CON", "unknown: %s  (try: seen, list, adc on/off, cal, stats)", line);
}

// ── 생명주기 ─────────────────────────────────
void setup() {
    logInit(115200);
    logInfo("BOOT", "HESTIA node=%s fw=%s schema=%d",
            nodeId(), FW_VERSION, SCHEMA_VERSION);

    inventoryBegin(NODE_SENSORS, NODE_DEVICES);
    displayId_ = findDeviceByType("display_node");
    sensorsBegin(&seqs, mqttPublishFn);
    devicesBegin(&seqs, mqttPublishFn);

    consoleBegin(onConsoleLine);

    #ifdef ACK_BUTTON_PIN
    buttonAttach(ackBtn_, ACK_BUTTON_PIN);
    logInfo("BOOT", "ack button pin=%d", ACK_BUTTON_PIN);
    #endif

    if (displayId_ != nullptr) {
        logInfo("BOOT", "display_node = %s", displayId_);
    }

    wifi.begin();
    mqtt.begin();
    mqtt.setMessageHandler(onMessage);

    if (displayId_ != nullptr) {
        mqtt.subscribe("hestia/notify/push", 1);
        mqtt.subscribe("hestia/notify/cancel", 1);
    }
    mqtt.subscribe("hestia/system/profile", 1);
    mqtt.subscribe("hestia/device/+/cmd", 1);
    mqtt.subscribe(SIM_CLOCK_TOPIC, 1);
}

void loop() {
    wifi.tick();

    bool wifiUp = wifi.isConnected();
    if (wifiUp && !wasWifiUp) {
        timeSyncBegin();
    }
    wasWifiUp = wifiUp;

    mqtt.tick();
    consoleTick();

    #ifdef ACK_BUTTON_PIN
    if (buttonPressed(ackBtn_)) {
        // 콘솔의 seen 과 같은 길. isForMe 로 걸러 들어온 것만 들고 있다
        const hestia::NotifyPush* top = notifies.latest();
        if (top != nullptr) {
            publishAck(top->notify_id, hestia::ACK_SEEN);
        } else {
            logInfo("BTN", "ack pressed — no active notify");
        }
    }
    #endif

    bool mqttUp = mqtt.isConnected();
    bool tsChanged = timeSyncTick();

    if (mqttUp && !wasMqttUp) {
        publishStatus();
        publishAnnounce();
        publishDeviceState();        // 부팅 직후 display:"" — retained 청소
        devicesPublishAll();
        sensorsResend();
    } else if (tsChanged && mqttUp) {
        publishStatus();
    }
    wasMqttUp = mqttUp;

    if (mqttUp && clockJumped_) {
        clockJumped_ = false;
        // retained 에 박힌 옛 sent_ts 를 새 시간대로 갈아끼운다
        publishStatus();
        publishAnnounce();
        sensorsResend();
    }

    if (mqttUp && millis() - lastHeartbeat >= HEARTBEAT_MS) {
        publishStatus();
    }
    if (mqttUp && millis() - lastDeviceState >= DEVICE_STATE_MS) {
        publishDeviceState();
        devicesPublishAll();
    }
    sensorsTick();

    delay(10);
}