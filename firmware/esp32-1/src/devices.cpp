#include "devices.h"
#include "inventory.h"
#include "logging.h"
#include "time_sync.h"
#include "devices/light.h"
#include "devices/fridge.h"
#include "devices/dispenser.h"
#include <cstring>

static hestia::SeqCounter* seqs_    = nullptr;
static DevicePublishFn     publish_ = nullptr;

bool deviceNextSeq(const hestia::DeviceSlot& slot, uint32_t& out_seq) {
    if (seqs_ == nullptr) return false;
    if (!seqs_->next(slot.virtual_id, out_seq)) {
        logError("DEV", "seq slots full for %s", slot.virtual_id);
        return false;
    }
    return true;
}

bool devicePublishState(const hestia::DeviceSlot& slot,
                        const std::string& payload) {
    if (publish_ == nullptr || payload.empty()) return false;
    std::string topic = hestia::deviceStateTopic(slot.virtual_id);
    if (!publish_(topic.c_str(), 1, true, payload.c_str())) {
        logWarn("DEV", "publish failed %s", slot.virtual_id);
        return false;
    }
    logInfo("DEV", "%s", payload.c_str());
    return true;
}

bool devicePublishEvent(const hestia::DeviceSlot& slot,
                        const std::string& payload) {
    if (publish_ == nullptr || payload.empty()) return false;
    std::string topic = hestia::deviceEventTopic(slot.virtual_id);
    // 이벤트는 retained 가 아니다 — 순간의 사건이다
    if (!publish_(topic.c_str(), 1, false, payload.c_str())) {
        logWarn("DEV", "event publish failed %s", slot.virtual_id);
        return false;
    }
    logInfo("DEV", "%s", payload.c_str());
    return true;
}

static void publishOne(const hestia::DeviceSlot& slot) {
    uint32_t seq = 0;

    if (strcmp(slot.device_type, "smart_light") == 0) {
        if (!deviceNextSeq(slot, seq)) return;
        devicePublishState(slot, lightStateJson(slot, seq, nowTs()));
    } else if (strcmp(slot.device_type, "smart_fridge") == 0) {
        if (!deviceNextSeq(slot, seq)) return;
        devicePublishState(slot, fridgeStateJson(slot, seq, nowTs()));
    }
    // display_node 는 main.cpp 가 다룬다.
    // water_purifier 는 상태가 없다 — 이벤트만 있는 기기다.
}

void devicesBegin(hestia::SeqCounter* seqs, DevicePublishFn publish) {
    seqs_    = seqs;
    publish_ = publish;

    for (uint8_t i = 0; i < deviceCount(); i++) {
        const hestia::DeviceSlot* d = deviceAt(i);
        if (d == nullptr) continue;

        if (strcmp(d->device_type, "smart_light") == 0) {
            lightBegin(*d);
        } else if (strcmp(d->device_type, "smart_fridge") == 0) {
            fridgeBegin(*d);
        } else if (strcmp(d->device_type, "water_purifier") == 0) {
            dispenserBegin(*d);
        }
    }
}

void devicesTick() {
    for (uint8_t i = 0; i < deviceCount(); i++) {
        const hestia::DeviceSlot* d = deviceAt(i);
        if (d == nullptr || d->pin == 255) continue;   // 버튼이 없는 기기

        if (strcmp(d->device_type, "smart_fridge") == 0) {
            fridgeTick(*d);
        } else if (strcmp(d->device_type, "water_purifier") == 0) {
            dispenserTick(*d);
        }
    }
}

void devicesHandleCmd(const char* virtual_id, const hestia::DeviceCmd& cmd) {
    for (uint8_t i = 0; i < deviceCount(); i++) {
        const hestia::DeviceSlot* d = deviceAt(i);
        if (d == nullptr) continue;
        if (strcmp(d->virtual_id, virtual_id) != 0) continue;

        // 토픽의 기기와 페이로드의 device_type 이 어긋나면 버린다
        if (strcmp(d->device_type, cmd.device_type) != 0) {
            logWarn("DEV", "%s is %s, cmd says %s — dropped",
                    virtual_id, d->device_type, cmd.device_type);
            return;
        }

        if (strcmp(d->device_type, "smart_light") == 0) {
            lightApply(*d, cmd);
            publishOne(*d);          // 반영 직후 발행
        } else {
            logWarn("DEV", "%s: no handler for %s",
                    virtual_id, d->device_type);
        }
        return;
    }
    // 내 기기가 아니다 — 조용히 무시
}

void devicesPublishAll() {
    for (uint8_t i = 0; i < deviceCount(); i++) {
        const hestia::DeviceSlot* d = deviceAt(i);
        if (d != nullptr) publishOne(*d);
    }
}