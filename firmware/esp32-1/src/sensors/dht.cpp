#include "dht.h"
#include "../sensors.h"
#include "../logging.h"
#include "../time_sync.h"
#include <Arduino.h>
#include <DHT.h>
#include <cstring>
#include <cmath>

static const uint8_t  MAX_DHT          = 2;
static const uint32_t READ_INTERVAL_MS = 10000;    // 센서 읽기
static const uint32_t PERIOD_MS        = 300000;   // 5분 주기 발행
// 칩별 임계 — DHT11 은 정수 단위라 작은 값이 의미 없다
static const float DELTA_T_11 = 1.0f;
static const float DELTA_H_11 = 5.0f;
static const float DELTA_T_22 = 0.3f;
static const float DELTA_H_22 = 2.0f;
static const uint8_t FAIL_WARN = 3;   // 임시. 확인 후 10 으로 복구

struct DhtState {
    const char* id;
    DHT*        dev;
    float       temp;
    float       hum;
    bool        valid;          // 한 번이라도 성공했는가
    uint32_t    lastRead;
    uint32_t    lastPublish;
    uint8_t     failCount;
    float       deltaT;
    float       deltaH;
};

static DhtState states_[MAX_DHT];
static uint8_t  count_ = 0;

static DhtState* findState(const char* virtual_id) {
    for (uint8_t i = 0; i < count_; i++) {
        if (strcmp(states_[i].id, virtual_id) == 0) {
            return &states_[i];
        }
    }
    return nullptr;
}

static void publishNow(const hestia::SensorSlot& slot, DhtState* st) {
    uint32_t seq = 0;
    if (!sensorNextSeq(slot, seq)) return;

    sensorPublish(slot,
        hestia::buildClimate(slot.virtual_id, seq, nowTs(), st->temp, st->hum));

    st->lastPublish = millis();
}

void dhtBegin(const hestia::SensorSlot& slot) {
    if (count_ >= MAX_DHT) {
        logError("DHT", "slots full, dropped %s", slot.virtual_id);
        return;
    }

    bool is11 = (strcmp(slot.kind, "dht11") == 0);

    DhtState* st = &states_[count_];

    st->id          = slot.virtual_id;
    st->dev         = new DHT(slot.pin, is11 ? DHT11 : DHT22);
    st->temp        = 0.0f;
    st->hum         = 0.0f;
    st->valid       = false;
    st->lastRead    = 0;
    st->lastPublish = 0;
    st->failCount   = 0;
    st->deltaT      = is11 ? DELTA_T_11 : DELTA_T_22;
    st->deltaH      = is11 ? DELTA_H_11 : DELTA_H_22;

    st->dev->begin();
    count_++;

    logInfo("DHT", "%s on pin %u (%s)",
            slot.virtual_id, slot.pin, is11 ? "DHT11" : "DHT22");
}

void dhtTick(const hestia::SensorSlot& slot) {
    DhtState* st = findState(slot.virtual_id);
    if (st == nullptr) return;

    uint32_t t = millis();
    if (t - st->lastRead < READ_INTERVAL_MS) return;
    st->lastRead = t;

    float temp = st->dev->readTemperature();
    float hum  = st->dev->readHumidity();

    if (isnan(temp) || isnan(hum)) {
        st->failCount++;
        if (st->failCount == FAIL_WARN) {
            logWarn("DHT", "%s read failed %u times in a row",
                    slot.virtual_id, st->failCount);
        }
        return;
    }

    if (st->failCount >= FAIL_WARN) {
        logInfo("DHT", "%s recovered after %u failures",
                slot.virtual_id, st->failCount);
    }
    st->failCount = 0;

    bool changed = !st->valid
                || fabsf(temp - st->temp) >= st->deltaT
                || fabsf(hum  - st->hum)  >= st->deltaH;
    bool due     = (t - st->lastPublish >= PERIOD_MS);

    st->temp  = temp;
    st->hum   = hum;
    st->valid = true;

    if (changed || due) {
        publishNow(slot, st);
    }
}

void dhtResend(const hestia::SensorSlot& slot) {
    DhtState* st = findState(slot.virtual_id);
    if (st == nullptr) return;

    if (!st->valid) {
        logWarn("DHT", "%s resend skipped, no valid reading yet",
                slot.virtual_id);
        return;
    }

    publishNow(slot, st);
}