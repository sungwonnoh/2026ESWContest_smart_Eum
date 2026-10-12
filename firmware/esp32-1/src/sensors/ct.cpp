#include "ct.h"
#include "../sensors.h"
#include "../logging.h"
#include "../time_sync.h"
#include <Arduino.h>
#include <cstring>
#include <cmath>

static const uint8_t  MAX_CT           = 1;
static const uint32_t SAMPLE_MS        = 100;     // 50Hz 5주기
static const uint32_t OFFSET_MS        = 500;
static const uint32_t READ_INTERVAL_MS = 1000;
static const uint32_t PERIOD_MS        = 300000;  // 5분
static const uint32_t DEBUG_PERIOD_MS  = 1000;

static const float    AMPS_PER_VOLT    = 30.0f;   // SCT-013-030
static const float    MAINS_VOLT       = 220.0f;
static const float    ADC_VOLT         = 3.3f / 4095.0f;
static const uint16_t WATT_DEADZONE    = 15;      // 이하는 0 으로
static const uint16_t WATT_DELTA       = 20;

struct CtState {
    const char* id;
    uint8_t     pin;
    uint16_t    offset;
    uint16_t    watt;
    bool        valid;
    uint32_t    lastRead;
    uint32_t    lastPublish;
};

static CtState  states_[MAX_CT];
static uint8_t  count_   = 0;
static bool     debug_   = false;
static uint32_t lastDbg_ = 0;

static CtState* findState(const char* virtual_id) {
    for (uint8_t i = 0; i < count_; i++) {
        if (strcmp(states_[i].id, virtual_id) == 0) {
            return &states_[i];
        }
    }
    return nullptr;
}

static uint16_t measureOffset(uint8_t pin) {
    uint32_t start = millis();
    uint32_t sum   = 0;
    uint32_t n     = 0;

    while (millis() - start < OFFSET_MS) {
        sum += analogRead(pin);
        n++;
    }
    return (n > 0) ? (uint16_t)(sum / n) : 2048;
}

// 100ms 동안의 RMS (ADC 단위)
static float measureRms(uint8_t pin, uint16_t offset) {
    uint32_t start = millis();
    uint64_t sum   = 0;
    uint32_t n     = 0;

    while (millis() - start < SAMPLE_MS) {
        int32_t d = (int32_t)analogRead(pin) - (int32_t)offset;
        sum += (uint64_t)(d * d);
        n++;
    }
    if (n == 0) return 0.0f;
    return sqrtf((float)sum / (float)n);
}

static uint16_t rmsToWatt(float rms) {
    float amps = rms * ADC_VOLT * AMPS_PER_VOLT;
    float w    = amps * MAINS_VOLT;

    if (w < (float)WATT_DEADZONE) return 0;
    if (w > 65535.0f)             return 65535;
    return (uint16_t)(w + 0.5f);
}

static void publishNow(const hestia::SensorSlot& slot, CtState* st) {
    uint32_t seq = 0;
    if (!sensorNextSeq(slot, seq)) return;

    sensorPublish(slot,
        hestia::buildPower(slot.virtual_id, seq, nowTs(), st->watt));

    st->lastPublish = millis();
}

void ctBegin(const hestia::SensorSlot& slot) {
    if (count_ >= MAX_CT) {
        logError("CT", "slots full, dropped %s", slot.virtual_id);
        return;
    }

    analogSetPinAttenuation(slot.pin, ADC_11db);

    CtState* st = &states_[count_];
    st->id          = slot.virtual_id;
    st->pin         = slot.pin;
    st->watt        = 0;
    st->valid       = false;
    st->lastRead    = 0;
    st->lastPublish = 0;
    st->offset      = measureOffset(slot.pin);
    count_++;

    logInfo("CT", "%s on pin %u, offset=%u",
            slot.virtual_id, slot.pin, st->offset);
}

void ctTick(const hestia::SensorSlot& slot) {
    CtState* st = findState(slot.virtual_id);
    if (st == nullptr) return;

    uint32_t t = millis();

    if (debug_ && t - lastDbg_ >= DEBUG_PERIOD_MS) {
        lastDbg_ = t;
        float rms = measureRms(st->pin, st->offset);
        logInfo("CT", "%s offset=%u rms=%.1f -> %uW",
                st->id, st->offset, rms, rmsToWatt(rms));
        return;                      // 디버그 중엔 발행 안 함
    }

    if (t - st->lastRead < READ_INTERVAL_MS) return;
    st->lastRead = t;

    float    rms  = measureRms(st->pin, st->offset);
    uint16_t watt = rmsToWatt(rms);

    uint16_t diff = (watt > st->watt) ? (watt - st->watt)
                                      : (st->watt - watt);
    bool changed = !st->valid || diff >= WATT_DELTA;
    bool due     = (t - st->lastPublish >= PERIOD_MS);

    st->watt  = watt;
    st->valid = true;

    if (changed || due) {
        publishNow(slot, st);
    }
}

void ctResend(const hestia::SensorSlot& slot) {
    CtState* st = findState(slot.virtual_id);
    if (st == nullptr) return;
    if (!st->valid) {
        logWarn("CT", "%s resend skipped, no reading yet", slot.virtual_id);
        return;
    }
    publishNow(slot, st);
}

void ctSetDebug(bool on) {
    debug_   = on;
    lastDbg_ = 0;
    logInfo("CT", "debug %s", on ? "on" : "off");
}

void ctCalibrate() {
    for (uint8_t i = 0; i < count_; i++) {
        uint16_t before = states_[i].offset;
        states_[i].offset = measureOffset(states_[i].pin);
        logInfo("CT", "%s offset %u -> %u",
                states_[i].id, before, states_[i].offset);
    }
}