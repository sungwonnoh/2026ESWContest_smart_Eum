#include "reed.h"
#include "../sensors.h"
#include "../logging.h"
#include "../time_sync.h"
#include <Arduino.h>
#include <cstring>

static const uint8_t  MAX_REED    = 4;
static const uint32_t DEBOUNCE_MS = 50;

struct ReedState {
    const char* id;
    bool        last;          // true = open
    uint32_t    lastChange;
};

static ReedState states_[MAX_REED];
static uint8_t   count_ = 0;

static ReedState* findState(const char* virtual_id) {
    for (uint8_t i = 0; i < count_; i++) {
        if (strcmp(states_[i].id, virtual_id) == 0) {
            return &states_[i];
        }
    }
    return nullptr;
}

// 자석이 가까우면(문 닫힘) 접점 ON → LOW. 풀업이라 열리면 HIGH
static bool readOpen(uint8_t pin) {
    return digitalRead(pin) == HIGH;
}

void reedBegin(const hestia::SensorSlot& slot) {
    if (count_ >= MAX_REED) {
        logError("REED", "slots full, dropped %s", slot.virtual_id);
        return;
    }

    pinMode(slot.pin, INPUT_PULLUP);

    states_[count_].id         = slot.virtual_id;
    states_[count_].last       = readOpen(slot.pin);
    states_[count_].lastChange = millis();
    count_++;

    logInfo("REED", "%s on pin %u, initial open=%d",
            slot.virtual_id, slot.pin, states_[count_ - 1].last ? 1 : 0);
}

void reedTick(const hestia::SensorSlot& slot) {
    ReedState* st = findState(slot.virtual_id);
    if (st == nullptr) return;

    bool now = readOpen(slot.pin);
    if (now == st->last) return;

    uint32_t t = millis();
    if (t - st->lastChange < DEBOUNCE_MS) return;

    st->last       = now;
    st->lastChange = t;

    uint32_t seq = 0;
    if (!sensorNextSeq(slot, seq)) return;

    sensorPublish(slot, hestia::buildDoor(slot.virtual_id, seq, nowTs(), now));
}

void reedResend(const hestia::SensorSlot& slot) {
    ReedState* st = findState(slot.virtual_id);
    if (st == nullptr) return;

    uint32_t seq = 0;
    if (!sensorNextSeq(slot, seq)) return;

    sensorPublish(slot, hestia::buildDoor(slot.virtual_id, seq, nowTs(), st->last));
}