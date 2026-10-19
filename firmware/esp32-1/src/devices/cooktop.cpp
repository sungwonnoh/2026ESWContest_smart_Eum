#include "devices/cooktop.h"
#include "devices.h"
#include "sensors/button.h"
#include "logging.h"
#include "time_sync.h"
#include <Arduino.h>

// 실제 ThinQ 쿡탑은 화구(locationName)마다 상태를 주고, 화력 범위도
// 모델마다 다르다. 엔진이 묻는 것은 '조리 중인가' 하나뿐이라 여기서는
// 한 화구로 접어 보낸다 — 화력과 화구 이름은 화면에만 쓰인다.
static const uint8_t POWER_LEVEL = 7;
static const char*   ZONE        = "LEFT_FRONT";

// 쿡탑은 한 대뿐이다. 여러 대가 되면 vid 로 짝지어야 한다.
static ButtonPin btn_;
static bool      cooking_ = false;

void cooktopBegin(const hestia::DeviceSlot& slot) {
    buttonAttach(btn_, slot.pin);
    cooking_ = false;
    logInfo("DEV", "%s cooktop button pin=%u", slot.virtual_id, slot.pin);
    // state 는 devicesPublishAll 이 연결 직후에 쏜다
}

void cooktopTick(const hestia::DeviceSlot& slot) {
    if (!buttonPressed(btn_)) return;

    cooking_ = !cooking_;

    uint32_t seq = 0;
    if (deviceNextSeq(slot, seq)) {
        devicePublishState(slot, cooktopStateJson(slot, seq, nowTs()));
    }
    logInfo("DEV", "%s %s", slot.virtual_id, cooking_ ? "COOK" : "INITIAL");
}

std::string cooktopStateJson(const hestia::DeviceSlot& slot,
                             uint32_t seq, uint32_t sent_ts) {
    return hestia::buildCooktopState(
        slot.virtual_id, seq, sent_ts, cooking_, POWER_LEVEL, ZONE);
}