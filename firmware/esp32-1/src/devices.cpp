#include "devices.h"
#include "inventory.h"
#include "logging.h"
#include "time_sync.h"
#include "devices/light.h"
#include <cstring>

static hestia::SeqCounter* seqs_    = nullptr;
static DevicePublishFn     publish_ = nullptr;

static bool publishState(const hestia::DeviceSlot& slot,
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

static void publishOne(const hestia::DeviceSlot& slot) {
    if (strcmp(slot.device_type, "smart_light") != 0) return;

    uint32_t seq = 0;
    if (seqs_ == nullptr) return;
    if (!seqs_->next(slot.virtual_id, seq)) {
        logError("DEV", "seq slots full for %s", slot.virtual_id);
        return;
    }

    publishState(slot, lightStateJson(slot, seq, nowTs()));
}

void devicesBegin(hestia::SeqCounter* seqs, DevicePublishFn publish) {
    seqs_    = seqs;
    publish_ = publish;

    for (uint8_t i = 0; i < deviceCount(); i++) {
        const hestia::DeviceSlot* d = deviceAt(i);
        if (d == nullptr) continue;

        if (strcmp(d->device_type, "smart_light") == 0) {
            lightBegin(*d);
        }
        // display_node 는 main.cpp 가 직접 다룬다
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