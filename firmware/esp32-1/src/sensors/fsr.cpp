#include "fsr.h"
#include "../sensors.h"
#include "../logging.h"
#include "../time_sync.h"
#include <Arduino.h>
#include <cstring>

static const uint8_t  MAX_FSR          = 2;
static const uint8_t  SAMPLES          = 8;
static const uint32_t READ_INTERVAL_MS = 200;
static const uint32_t SETTLE_MS        = 1500;   // 판정 후 유지 시간
static const uint32_t DEBUG_PERIOD_MS  = 1000;

// 실측 후 조정할 값
static const uint16_t TH_ON  = 1800;
static const uint16_t TH_OFF = 1400;

struct FsrState {
    const char* id;
    uint8_t     pin;
    bool        occupied;
    uint32_t    lastRead;
    uint32_t    lastChange;
};

static FsrState states_[MAX_FSR];
static uint8_t  count_    = 0;
static bool     debug_    = false;
static uint32_t lastDbg_  = 0;

static FsrState* findState(const char* virtual_id) {
    for (uint8_t i = 0; i < count_; i++) {
        if (strcmp(states_[i].id, virtual_id) == 0) {
            return &states_[i];
        }
    }
    return nullptr;
}

static uint16_t readAveraged(uint8_t pin) {
    uint32_t sum = 0;
    for (uint8_t i = 0; i < SAMPLES; i++) {
        sum += analogRead(pin);
    }
    return (uint16_t)(sum / SAMPLES);
}

static void publishNow(const hestia::SensorSlot& slot, FsrState* st) {
    uint32_t seq = 0;
    if (!sensorNextSeq(slot, seq)) return;

    sensorPublish(slot,
        hestia::buildBed(slot.virtual_id, seq, nowTs(), st->occupied));
}

void fsrBegin(const hestia::SensorSlot& slot) {
    if (count_ >= MAX_FSR) {
        logError("FSR", "slots full, dropped %s", slot.virtual_id);
        return;
    }

    analogSetPinAttenuation(slot.pin, ADC_11db);   // 0~3.3V 전체 범위

    FsrState* st = &states_[count_];
    st->id         = slot.virtual_id;
    st->pin        = slot.pin;
    st->lastRead   = 0;
    st->lastChange = millis();

    uint16_t raw   = readAveraged(slot.pin);
    st->occupied   = (raw >= TH_ON);
    count_++;

    logInfo("FSR", "%s on pin %u, raw=%u occupied=%d",
            slot.virtual_id, slot.pin, raw, st->occupied ? 1 : 0);
}

void fsrTick(const hestia::SensorSlot& slot) {
    FsrState* st = findState(slot.virtual_id);
    if (st == nullptr) return;

    uint32_t t = millis();

    if (debug_ && t - lastDbg_ >= DEBUG_PERIOD_MS) {
        lastDbg_ = t;
        logInfo("FSR", "%s raw=%u (on>=%u off<=%u) occupied=%d",
                st->id, readAveraged(st->pin), TH_ON, TH_OFF,
                st->occupied ? 1 : 0);
    }

    if (t - st->lastRead < READ_INTERVAL_MS) return;
    st->lastRead = t;

    uint16_t raw = readAveraged(st->pin);

    bool next = st->occupied;
    if (!st->occupied && raw >= TH_ON)  next = true;
    if (st->occupied  && raw <= TH_OFF) next = false;

    if (next == st->occupied) return;
    if (t - st->lastChange < SETTLE_MS) return;

    st->occupied   = next;
    st->lastChange = t;

    publishNow(slot, st);
}

void fsrResend(const hestia::SensorSlot& slot) {
    FsrState* st = findState(slot.virtual_id);
    if (st == nullptr) return;
    publishNow(slot, st);
}

void fsrSetDebug(bool on) {
    debug_   = on;
    lastDbg_ = 0;
    logInfo("FSR", "debug %s", on ? "on" : "off");
}