#include "bh1750.h"
#include "../sensors.h"
#include "../logging.h"
#include "../time_sync.h"
#include "../i2c_bus.h"
#include <Arduino.h>
#include <BH1750.h>
#include <cstring>

static const uint8_t  SCL_PIN          = 22;     // SDA 는 slot.pin
static const uint8_t  I2C_ADDR         = 0x23;
static const uint32_t READ_INTERVAL_MS = 2000;
static const uint32_t PERIOD_MS        = 300000; // 5분
static const uint8_t  FAIL_WARN        = 10;

// 상대 변화 20%, 단 최소 10lux 는 움직여야
static const float    REL_DELTA        = 0.20f;
static const uint32_t ABS_DELTA_MIN    = 10;

static BH1750      dev_(I2C_ADDR);
static const char* id_        = nullptr;
static uint32_t    lux_       = 0;
static bool        valid_     = false;
static uint32_t    lastRead_  = 0;
static uint32_t    lastPub_   = 0;
static uint8_t     failCount_ = 0;

static void publishNow(const hestia::SensorSlot& slot) {
    uint32_t seq = 0;
    if (!sensorNextSeq(slot, seq)) return;

    sensorPublish(slot,
        hestia::buildLight(slot.virtual_id, seq, nowTs(), lux_));
    lastPub_ = millis();
}

// 어두울 때의 10lux 와 밝을 때의 10lux 는 의미가 다르다
static bool changedEnough(uint32_t next) {
    if (!valid_) return true;

    uint32_t diff = (next > lux_) ? (next - lux_) : (lux_ - next);
    if (diff < ABS_DELTA_MIN) return false;

    uint32_t base = (lux_ > next) ? lux_ : next;
    if (base == 0) return true;

    return ((float)diff / (float)base) >= REL_DELTA;
}

void bh1750Begin(const hestia::SensorSlot& slot) {
    if (id_ != nullptr) {
        logError("LUX", "only one bh1750 per node, dropped %s",
                 slot.virtual_id);
        return;
    }

    i2cBegin(slot.pin, SCL_PIN);

    if (!dev_.begin(BH1750::CONTINUOUS_HIGH_RES_MODE)) {
        logError("LUX", "%s init failed at 0x%02X (check wiring/addr)",
                 slot.virtual_id, I2C_ADDR);
        return;
    }

    id_ = slot.virtual_id;
    logInfo("LUX", "%s sda=%u scl=%u addr=0x%02X",
            slot.virtual_id, slot.pin, SCL_PIN, I2C_ADDR);
}

void bh1750Tick(const hestia::SensorSlot& slot) {
    if (id_ == nullptr) return;

    uint32_t t = millis();
    if (t - lastRead_ < READ_INTERVAL_MS) return;
    lastRead_ = t;

    if (!dev_.measurementReady(false)) return;

    float raw = dev_.readLightLevel();
    if (raw < 0.0f) {
        failCount_++;
        if (failCount_ == FAIL_WARN) {
            logWarn("LUX", "%s read failed %u times in a row",
                    id_, failCount_);
        }
        return;
    }

    if (failCount_ >= FAIL_WARN) {
        logInfo("LUX", "%s recovered after %u failures", id_, failCount_);
    }
    failCount_ = 0;

    uint32_t next = (raw > 65535.0f) ? 65535 : (uint32_t)(raw + 0.5f);

    bool changed = changedEnough(next);
    bool due     = (t - lastPub_ >= PERIOD_MS);

    lux_   = next;
    valid_ = true;

    if (changed || due) {
        publishNow(slot);
    }
}

void bh1750Resend(const hestia::SensorSlot& slot) {
    if (id_ == nullptr) return;
    if (!valid_) {
        logWarn("LUX", "%s resend skipped, no reading yet", slot.virtual_id);
        return;
    }
    publishNow(slot);
}