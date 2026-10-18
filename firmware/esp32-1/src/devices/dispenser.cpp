#include "devices/dispenser.h"
#include "devices.h"
#include "sensors/button.h"
#include "logging.h"
#include "time_sync.h"

// 상태가 없다. 누를 때마다 한 잔이다.
static ButtonPin btn_;

static const uint16_t AMOUNT_ML = 200;

void dispenserBegin(const hestia::DeviceSlot& slot) {
    buttonAttach(btn_, slot.pin);
    logInfo("DEV", "%s dispenser button pin=%u", slot.virtual_id, slot.pin);
}

void dispenserTick(const hestia::DeviceSlot& slot) {
    if (!buttonPressed(btn_)) return;

    uint32_t seq = 0;
    if (!deviceNextSeq(slot, seq)) return;

    devicePublishEvent(slot, hestia::buildDispensed(
        slot.virtual_id, seq, nowTs(), "AMBIENT", AMOUNT_ML));
    logInfo("DEV", "%s dispensed %uml", slot.virtual_id, AMOUNT_ML);
}