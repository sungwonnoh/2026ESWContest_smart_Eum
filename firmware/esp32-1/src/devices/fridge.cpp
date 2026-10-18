#include "devices/fridge.h"
#include "devices.h"
#include "sensors/button.h"
#include "logging.h"
#include "time_sync.h"
#include <Arduino.h>

// 냉장고는 한 대뿐이다. 여러 대가 되면 vid 로 짝지어야 한다.
static ButtonPin btn_;
static bool      open_     = false;
static uint32_t  openedMs_ = 0;

void fridgeBegin(const hestia::DeviceSlot& slot) {
    buttonAttach(btn_, slot.pin);
    open_ = false;
    logInfo("DEV", "%s fridge button pin=%u", slot.virtual_id, slot.pin);
    // state 는 devicesPublishAll 이 연결 직후에 쏜다
}

void fridgeTick(const hestia::DeviceSlot& slot) {
    if (!buttonPressed(btn_)) return;

    open_ = !open_;
    uint32_t seq = 0;

    if (open_) {
        // 실제로 열려 있던 시간을 재려고 실시간(millis)을 쓴다.
        // 가상 시계를 쓰면 점프했을 때 몇 시간 열어둔 것이 된다.
        openedMs_ = millis();
        if (deviceNextSeq(slot, seq)) {
            devicePublishEvent(slot, hestia::buildDeviceEvent(
                slot.virtual_id, seq, nowTs(), "smart_fridge", "door_opened"));
        }
    } else {
        float held = (millis() - openedMs_) / 1000.0f;
        if (deviceNextSeq(slot, seq)) {
            devicePublishEvent(slot, hestia::buildDoorClosed(
                slot.virtual_id, seq, nowTs(), "smart_fridge", held));
        }
    }

    if (deviceNextSeq(slot, seq)) {
        devicePublishState(slot, fridgeStateJson(slot, seq, nowTs()));
    }
}

std::string fridgeStateJson(const hestia::DeviceSlot& slot,
                            uint32_t seq, uint32_t sent_ts) {
    return hestia::buildFridgeState(slot.virtual_id, seq, sent_ts, open_);
}