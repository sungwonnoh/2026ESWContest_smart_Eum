#include "inventory.h"
#include "logging.h"
#include <cstring>
#include <cstdlib>

static const uint8_t MAX_SENSORS = 8;
static const uint8_t MAX_DEVICES = 8;

// 플래그 문자열의 복사본. strtok 가 원본을 수정하므로 리터럴을 직접 쪼갤 수 없고,
// 쪼갠 조각들의 주소를 아래 배열이 계속 들고 있으므로 static 이어야 한다.
static char sensorBuf_[320];
static char deviceBuf_[256];

static hestia::SensorSlot sensors_[MAX_SENSORS];
static uint8_t            sensorCount_ = 0;

static hestia::DeviceSlot devices_[MAX_DEVICES];
static uint8_t            deviceCount_ = 0;

// ── 파싱 ─────────────────────────────────────
static void parseSensors(char* buf) {
    char* save1 = nullptr;
    char* item  = strtok_r(buf, ",", &save1);

    while (item != nullptr) {
        if (sensorCount_ >= MAX_SENSORS) {
            logError("INV", "sensor slots full, dropped %s", item);
            break;
        }

        char* save2 = nullptr;
        char* vid   = strtok_r(item, ":", &save2);
        char* type  = strtok_r(nullptr, ":", &save2);
        char* kind  = strtok_r(nullptr, ":", &save2);
        char* pin   = strtok_r(nullptr, ":", &save2);

        if (vid == nullptr || type == nullptr || kind == nullptr || pin == nullptr) {
            logError("INV", "malformed sensor entry, skipped");
        } else {
            hestia::SensorType st;
            if (!hestia::parseSensorType(type, st)) {
                logError("INV", "unknown sensor type \"%s\", skipped %s", type, vid);
            } else {
                sensors_[sensorCount_].virtual_id = vid;
                sensors_[sensorCount_].type       = st;
                sensors_[sensorCount_].kind       = kind;
                sensors_[sensorCount_].pin        = (uint8_t)atoi(pin);
                sensorCount_++;
            }
        }
        item = strtok_r(nullptr, ",", &save1);
    }
}

// "vd-02:smart_light:26" 또는 "vd-10:display_node" — 핀은 선택
static void parseDevices(char* buf) {
    char* save1 = nullptr;
    char* item  = strtok_r(buf, ",", &save1);

    while (item != nullptr) {
        if (deviceCount_ >= MAX_DEVICES) {
            logError("INV", "device slots full, dropped %s", item);
            break;
        }

        char* save2 = nullptr;
        char* vid   = strtok_r(item, ":", &save2);
        char* dtype = strtok_r(nullptr, ":", &save2);
        char* pin   = strtok_r(nullptr, ":", &save2);

        if (vid == nullptr || dtype == nullptr) {
            logError("INV", "malformed device entry, skipped");
        } else {
            devices_[deviceCount_].virtual_id  = vid;
            devices_[deviceCount_].device_type = dtype;
            devices_[deviceCount_].pin =
                (pin != nullptr) ? (uint8_t)atoi(pin) : 255;
            deviceCount_++;
        }
        item = strtok_r(nullptr, ",", &save1);
    }
}

void inventoryBegin(const char* sensors_flag, const char* devices_flag) {
    sensorCount_ = 0;
    deviceCount_ = 0;

    if (sensors_flag != nullptr && sensors_flag[0] != '\0') {
        strncpy(sensorBuf_, sensors_flag, sizeof(sensorBuf_) - 1);
        sensorBuf_[sizeof(sensorBuf_) - 1] = '\0';
        parseSensors(sensorBuf_);
    }

    if (devices_flag != nullptr && devices_flag[0] != '\0') {
        strncpy(deviceBuf_, devices_flag, sizeof(deviceBuf_) - 1);
        deviceBuf_[sizeof(deviceBuf_) - 1] = '\0';
        parseDevices(deviceBuf_);
    }

    for (uint8_t i = 0; i < sensorCount_; i++) {
        logInfo("INV", "sensor %s type=%s kind=%s pin=%u",
                sensors_[i].virtual_id,
                hestia::typeName(sensors_[i].type),
                sensors_[i].kind,
                sensors_[i].pin);
    }
    for (uint8_t i = 0; i < deviceCount_; i++) {
        if (devices_[i].pin == 255) {
            logInfo("INV", "device %s type=%s",
                    devices_[i].virtual_id, devices_[i].device_type);
        } else {
            logInfo("INV", "device %s type=%s pin=%u",
                    devices_[i].virtual_id, devices_[i].device_type,
                    devices_[i].pin);
        }
    }
}

// ── 조회 ─────────────────────────────────────
uint8_t sensorCount() { return sensorCount_; }

const hestia::SensorSlot* sensorAt(uint8_t i) {
    return (i < sensorCount_) ? &sensors_[i] : nullptr;
}

uint8_t deviceCount() { return deviceCount_; }

const hestia::DeviceSlot* deviceAt(uint8_t i) {
    return (i < deviceCount_) ? &devices_[i] : nullptr;
}

const char* deviceVirtualIdAt(uint8_t i) {
    return (i < deviceCount_) ? devices_[i].virtual_id : nullptr;
}

const char* deviceTypeAt(uint8_t i) {
    return (i < deviceCount_) ? devices_[i].device_type : nullptr;
}

const char* findDeviceByType(const char* device_type) {
    if (device_type == nullptr) return nullptr;
    for (uint8_t i = 0; i < deviceCount_; i++) {
        if (strcmp(devices_[i].device_type, device_type) == 0) {
            return devices_[i].virtual_id;
        }
    }
    return nullptr;
}

uint8_t buildEmulates(hestia::Emulated* out, uint8_t cap) {
    uint8_t n = 0;
    for (uint8_t i = 0; i < sensorCount_ && n < cap; i++) {
        out[n++] = hestia::sensorEmulated(sensors_[i].virtual_id, sensors_[i].type);
    }
    for (uint8_t i = 0; i < deviceCount_ && n < cap; i++) {
        out[n++] = hestia::deviceEmulated(devices_[i].virtual_id, devices_[i].device_type);
    }
    return n;
}