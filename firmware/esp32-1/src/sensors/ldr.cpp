#include "ldr.h"
#include "../sensors.h"
#include "../logging.h"
#include "../time_sync.h"
#include <Arduino.h>

// 두 점 보간. LDR 은 개체차가 크고 책상 조명에 따라 값이 달라지므로
// 숫자를 코드에 박지 않고 build_flags 로 맞춘다.
//
// 선형 보간은 물리적으로 정확하지 않다 — LDR 의 저항·조도 관계는 로그
// 스케일이다. 다만 DAY_LIGHT 가 묻는 것은 "몇 lux 인가" 가 아니라
// "floor_lux 아래인가 ceiling_lux 위인가" 뿐이고, 그 순서는 보존된다.
#ifndef LDR_DARK_ADC
#define LDR_DARK_ADC    500      // 손으로 덮었을 때
#endif
#ifndef LDR_BRIGHT_ADC
#define LDR_BRIGHT_ADC  3000     // 평소 책상
#endif
#ifndef LDR_DARK_LUX
#define LDR_DARK_LUX    50
#endif
#ifndef LDR_BRIGHT_LUX
#define LDR_BRIGHT_LUX  800
#endif

static const uint32_t READ_INTERVAL_MS = 500;
static const uint32_t PERIOD_MS        = 300000;  // 5분
static const uint8_t  SAMPLES          = 16;      // ESP32 ADC 는 한 번 읽으면 튄다

// 어두울 때의 10lux 와 밝을 때의 10lux 는 의미가 다르다 (bh1750 과 동일)
static const float    REL_DELTA     = 0.20f;
static const uint32_t ABS_DELTA_MIN = 10;

static const char* id_       = nullptr;
static uint8_t     pin_      = 0;
static uint32_t    lux_      = 0;
static bool        valid_    = false;
static uint32_t    lastRead_ = 0;
static uint32_t    lastPub_  = 0;

static void publishNow(const hestia::SensorSlot& slot) {
    uint32_t seq = 0;
    if (!sensorNextSeq(slot, seq)) return;

    sensorPublish(slot,
        hestia::buildLight(slot.virtual_id, seq, nowTs(), lux_));
    lastPub_ = millis();
}

static bool changedEnough(uint32_t next) {
    if (!valid_) return true;

    uint32_t diff = (next > lux_) ? (next - lux_) : (lux_ - next);
    if (diff < ABS_DELTA_MIN) return false;

    uint32_t base = (lux_ > next) ? lux_ : next;
    if (base == 0) return true;

    return ((float)diff / (float)base) >= REL_DELTA;
}

static uint32_t readAdc() {
    uint32_t sum = 0;
    for (uint8_t i = 0; i < SAMPLES; i++) {
        sum += (uint32_t)analogRead(pin_);
    }
    return sum / SAMPLES;
}

static uint32_t toLux(uint32_t adc) {
    const float da = (float)LDR_DARK_ADC;
    const float ba = (float)LDR_BRIGHT_ADC;
    const float dl = (float)LDR_DARK_LUX;
    const float bl = (float)LDR_BRIGHT_LUX;

    if (ba == da) return (uint32_t)dl;      // 설정이 잘못됐다. 나누지 않는다

    // 배선 방향(풀업·풀다운)에 따라 da > ba 일 수도 있다. 그대로 성립한다
    float t = ((float)adc - da) / (ba - da);
    if (t < 0.0f) t = 0.0f;
    if (t > 1.0f) t = 1.0f;

    return (uint32_t)(dl + t * (bl - dl) + 0.5f);
}

void ldrBegin(const hestia::SensorSlot& slot) {
    if (id_ != nullptr) {
        logError("LUX", "only one ldr per node, dropped %s", slot.virtual_id);
        return;
    }
    // ADC2 는 WiFi 가 켜지면 쓸 수 없다. 조용히 0 만 읽히는 것보다 낫다
    if (slot.pin < 32 || slot.pin > 39) {
        logError("LUX", "%s pin %u is not ADC1 (32~39) — WiFi disables ADC2",
                 slot.virtual_id, slot.pin);
        return;
    }

    pin_ = slot.pin;
    analogSetPinAttenuation(pin_, ADC_11db);     // 0~3.3V 대역

    id_ = slot.virtual_id;
    logInfo("LUX", "%s adc=%u  dark %d->%dlux  bright %d->%dlux",
            slot.virtual_id, pin_,
            LDR_DARK_ADC, LDR_DARK_LUX, LDR_BRIGHT_ADC, LDR_BRIGHT_LUX);
}

void ldrTick(const hestia::SensorSlot& slot) {
    if (id_ == nullptr) return;

    uint32_t t = millis();
    if (t - lastRead_ < READ_INTERVAL_MS) return;
    lastRead_ = t;

    uint32_t adc  = readAdc();
    uint32_t next = toLux(adc);

#ifdef LDR_DEBUG
    logInfo("LUX", "adc=%u lux=%u", adc, next);
#endif

    bool changed = changedEnough(next);
    bool due     = (t - lastPub_ >= PERIOD_MS);

    lux_   = next;
    valid_ = true;

    if (changed || due) {
        publishNow(slot);
    }
}

void ldrResend(const hestia::SensorSlot& slot) {
    if (id_ == nullptr) return;
    if (!valid_) {
        logWarn("LUX", "%s resend skipped, no reading yet", slot.virtual_id);
        return;
    }
    publishNow(slot);
}