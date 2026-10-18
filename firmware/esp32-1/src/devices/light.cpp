#include "light.h"
#include "../logging.h"
#include <Arduino.h>
#include <cstring>

static const uint8_t  MAX_LIGHTS  = 4;
static const uint32_t PWM_FREQ    = 5000;
static const uint8_t  PWM_BITS    = 8;      // 0~255

struct LightState {
    const char* id;
    uint8_t     pin;
    uint8_t     channel;        // ledc 채널

    // 보고용 — 엔진에서 받은 값 그대로. 변환하지 않는다
    char     power[12];
    uint8_t  brightness;
    uint16_t color_temp;
    bool     has_brightness;
    bool     has_color_temp;
};

static LightState states_[MAX_LIGHTS];
static uint8_t    count_ = 0;

static LightState* findState(const char* virtual_id) {
    for (uint8_t i = 0; i < count_; i++) {
        if (strcmp(states_[i].id, virtual_id) == 0) {
            return &states_[i];
        }
    }
    return nullptr;
}

// 보고값에서 구동값을 만든다. 역방향은 절대 하지 않는다
static void drive(LightState* st) {
    bool on = (strcmp(st->power, "ON") == 0);

    uint8_t pct = st->has_brightness ? st->brightness : 100;
    uint8_t duty = on ? (uint8_t)(((uint16_t)pct * 255) / 100) : 0;

    ledcWrite(st->channel, duty);

    logInfo("LIGHT", "%s power=%s brightness=%u -> duty=%u",
            st->id, st->power, pct, duty);
}

void lightBegin(const hestia::DeviceSlot& slot) {
    if (count_ >= MAX_LIGHTS) {
        logError("LIGHT", "slots full, dropped %s", slot.virtual_id);
        return;
    }
    if (slot.pin == 255) {
        logError("LIGHT", "%s has no pin, skipped", slot.virtual_id);
        return;
    }

    LightState* st = &states_[count_];

    st->id      = slot.virtual_id;
    st->pin     = slot.pin;
    st->channel = count_;

    // 부팅 기본값 — 꺼진 상태. brightness 는 아직 받은 적 없다
    strncpy(st->power, "OFF", sizeof(st->power) - 1);
    st->power[sizeof(st->power) - 1] = '\0';
    st->brightness     = 0;
    st->color_temp     = 0;
    st->has_brightness = false;
    st->has_color_temp = false;

    ledcSetup(st->channel, PWM_FREQ, PWM_BITS);
    ledcAttachPin(st->pin, st->channel);
    ledcWrite(st->channel, 0);

    count_++;

    logInfo("LIGHT", "%s on pin %u (ch%u)",
            slot.virtual_id, slot.pin, st->channel);
}

bool lightApply(const hestia::DeviceSlot& slot, const hestia::DeviceCmd& cmd) {
    LightState* st = findState(slot.virtual_id);
    if (st == nullptr) return false;

    if (strcmp(cmd.action, "set") != 0) {
        logWarn("LIGHT", "%s unsupported action \"%s\"",
                st->id, cmd.action);
        return false;
    }

    bool changed = false;

    // params 에 없는 키는 건드리지 않는다
    if (cmd.light.has_power) {
        if (strcmp(st->power, cmd.light.power) != 0) changed = true;
        strncpy(st->power, cmd.light.power, sizeof(st->power) - 1);
        st->power[sizeof(st->power) - 1] = '\0';
    }

    if (cmd.light.has_brightness) {
        if (!st->has_brightness || st->brightness != cmd.light.brightness) {
            changed = true;
        }
        st->brightness     = cmd.light.brightness;
        st->has_brightness = true;
    }

    if (cmd.light.has_color_temp) {
        if (!st->has_color_temp || st->color_temp != cmd.light.color_temp) {
            changed = true;
        }
        st->color_temp     = cmd.light.color_temp;
        st->has_color_temp = true;
        // 단색 LED 라 구동에는 반영하지 않는다. 보고만 한다
    }

    // power 와 brightness 가 같이 와도 한 번에 적용된다
    drive(st);
    return changed;
}

std::string lightStateJson(const hestia::DeviceSlot& slot,
                           uint32_t seq, uint32_t sent_ts) {
    LightState* st = findState(slot.virtual_id);
    if (st == nullptr) return std::string();

    return hestia::buildSmartLightState(
        slot.virtual_id, seq, sent_ts,
        st->power,
        st->has_brightness, st->brightness,
        st->has_color_temp, st->color_temp);
}