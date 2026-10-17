#include "sensors/button.h"
#include "sensors.h"
#include "logging.h"
#include "time_sync.h"
#include <Arduino.h>
#include <cstring>

static const uint32_t DEBOUNCE_MS = 50;

// 켜짐으로 읽히기에 충분한 값. ON/OFF 판정은 엔진의 power_profile 이 한다 —
// buildPower 에 state 가 없는 것이 그 뜻이다. 실제 CT 도 watt 만 안다.
static const uint16_t WATT_ON  = 1200;
static const uint16_t WATT_OFF = 0;

// ── 디바운스 ─────────────────────────────────

void buttonAttach(ButtonPin& b, uint8_t pin) {
    b.pin       = pin;
    b.stable    = true;
    b.lastRaw   = true;
    b.changedMs = millis();
    pinMode(pin, INPUT_PULLUP);
}

bool buttonPressed(ButtonPin& b) {
    if (b.pin == 255) return false;

    bool     raw = (digitalRead(b.pin) != LOW);   // true = 안 눌림
    uint32_t now = millis();

    if (raw != b.lastRaw) {                       // 흔들리는 중
        b.lastRaw   = raw;
        b.changedMs = now;
        return false;
    }
    if (now - b.changedMs < DEBOUNCE_MS) return false;
    if (raw == b.stable)                 return false;

    b.stable = raw;
    return !raw;                                  // HIGH→LOW 로 안정 = 눌림
}

// ── 슬롯별 상태 ──────────────────────────────
// SensorSlot 에 저장 공간이 없어 여기서 vid 로 짝지어 들고 있는다.

static const uint8_t MAX_BUTTONS = 6;

struct Entry {
    const char*        vid  = nullptr;
    hestia::SensorType type = hestia::SensorType::Power;
    ButtonPin          btn;
    bool               on   = false;
};

static Entry   entries_[MAX_BUTTONS];
static uint8_t count_ = 0;

static Entry* find(const hestia::SensorSlot& slot) {
    for (uint8_t i = 0; i < count_; i++) {
        if (entries_[i].vid != nullptr &&
            strcmp(entries_[i].vid, slot.virtual_id) == 0) {
            return &entries_[i];
        }
    }
    return nullptr;
}

static void emit(const hestia::SensorSlot& slot, const Entry& e) {
    uint32_t seq = 0;
    if (!sensorNextSeq(slot, seq)) return;

    std::string payload;
    switch (e.type) {
        case hestia::SensorType::Power:
            payload = hestia::buildPower(slot.virtual_id, seq, nowTs(),
                                         e.on ? WATT_ON : WATT_OFF);
            break;
        case hestia::SensorType::Bed:
            payload = hestia::buildBed(slot.virtual_id, seq, nowTs(), e.on);
            break;
        default:
            return;                     // Begin 에서 걸렀어야 한다
    }
    sensorPublish(slot, payload);
}

// ── 드라이버 ─────────────────────────────────

void buttonBegin(const hestia::SensorSlot& slot) {
    if (slot.type != hestia::SensorType::Power &&
        slot.type != hestia::SensorType::Bed) {
        logError("BTN", "%s: button 은 power/bed 만 흉내 낸다",
                 slot.virtual_id);
        return;
    }
    if (count_ >= MAX_BUTTONS) {
        logError("BTN", "slots full for %s", slot.virtual_id);
        return;
    }

    Entry& e = entries_[count_++];
    e.vid  = slot.virtual_id;
    e.type = slot.type;
    e.on   = false;
    buttonAttach(e.btn, slot.pin);
    logInfo("BTN", "%s %s pin=%u",
            slot.virtual_id, hestia::typeName(slot.type), slot.pin);

    // 꺼짐을 retained 에 박아둔다. 안 박으면 엔진이 '모른다' 로 본다
    emit(slot, e);
}

void buttonTick(const hestia::SensorSlot& slot) {
    Entry* e = find(slot);
    if (e == nullptr) return;
    if (!buttonPressed(e->btn)) return;

    e->on = !e->on;
    logInfo("BTN", "%s -> %s", slot.virtual_id, e->on ? "ON" : "OFF");
    emit(slot, *e);
}

void buttonResend(const hestia::SensorSlot& slot) {
    Entry* e = find(slot);
    if (e != nullptr) emit(slot, *e);
}