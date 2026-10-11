#include "pir.h"
#include "../sensors.h"
#include "../logging.h"
#include "../time_sync.h"
#include <Arduino.h>
#include <cstring>

static const uint8_t  MAX_PIR    = 4;
static const uint32_t DEBOUNCE_MS = 200;

struct PirState {
    const char* id;
    bool        last;
    uint32_t    lastChange;
};

static PirState states_[MAX_PIR];
static uint8_t  count_ = 0;

static PirState* findState(const char* virtual_id) {
    for (uint8_t i = 0; i < count_; i++) {
        if (strcmp(states_[i].id, virtual_id) == 0) {
            return &states_[i];
        }
    }
    return nullptr;
}

void pirBegin(const hestia::SensorSlot& slot) {
    if (count_ >= MAX_PIR) {
        logError("PIR", "slots full, dropped %s", slot.virtual_id);
        return;
    }

    pinMode(slot.pin, INPUT);

    states_[count_].id         = slot.virtual_id;
    states_[count_].last       = (digitalRead(slot.pin) == HIGH);
    states_[count_].lastChange = millis();
    count_++;

    logInfo("PIR", "%s on pin %u, initial=%d",
            slot.virtual_id, slot.pin, states_[count_ - 1].last ? 1 : 0);
}

void pirTick(const hestia::SensorSlot& slot) {
    PirState* st = findState(slot.virtual_id);
    if (st == nullptr) return;

    bool now = (digitalRead(slot.pin) == HIGH);
    if (now == st->last) return;                       // 변화 없음

    uint32_t t = millis();
    if (t - st->lastChange < DEBOUNCE_MS) return;      // 너무 빠른 반전은 무시

    st->last       = now;
    st->lastChange = t;

    uint32_t seq = 0;
    if (!sensorNextSeq(slot, seq)) return;

    sensorPublish(slot, hestia::buildMotion(slot.virtual_id, seq, nowTs(), now));
}

void pirResend(const hestia::SensorSlot& slot) {
    (void)slot;
    // motion 은 순간 이벤트이고 retained 가 아니다.
    // "지금 움직임이 있다" 를 재전송하는 것은 의미가 없다.
}