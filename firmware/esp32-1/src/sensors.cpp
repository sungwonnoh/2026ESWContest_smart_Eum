#include "sensors.h"
#include "inventory.h"
#include "logging.h"
#include "time_sync.h"
#include "sensors/pir.h"
#include "sensors/reed.h"
#include "sensors/dht22.h"
#include "sensors/fsr.h"
#include "sensors/ct.h"
#include "sensors/ld2410.h"
#include <cstring>

static hestia::SeqCounter* seqs_    = nullptr;
static SensorPublishFn     publish_ = nullptr;

// ── 헬퍼 ─────────────────────────────────────
bool sensorNextSeq(const hestia::SensorSlot& slot, uint32_t& out_seq) {
    if (seqs_ == nullptr) return false;
    if (!seqs_->next(slot.virtual_id, out_seq)) {
        logError("SENS", "seq slots full for %s", slot.virtual_id);
        return false;
    }
    return true;
}

bool sensorPublish(const hestia::SensorSlot& slot, const std::string& payload) {
    if (publish_ == nullptr) return false;

    std::string topic = hestia::sensorStateTopic(slot.virtual_id);
    uint8_t qos       = hestia::qosFor(slot.type);
    bool    retain    = hestia::isRetained(slot.type);

    if (!publish_(topic.c_str(), qos, retain, payload.c_str())) {
        logWarn("SENS", "publish failed %s", slot.virtual_id);
        return false;
    }
    logInfo("SENS", "%s", payload.c_str());
    return true;
}

// ── 디스패치 ─────────────────────────────────
void sensorsBegin(hestia::SeqCounter* seqs, SensorPublishFn publish) {
    seqs_    = seqs;
    publish_ = publish;

    for (uint8_t i = 0; i < sensorCount(); i++) {
        const hestia::SensorSlot* s = sensorAt(i);
        if (s == nullptr) continue;

        if (strcmp(s->kind, "pir") == 0) {
            pirBegin(*s);
        } else if (strcmp(s->kind, "reed") == 0) {
            reedBegin(*s);
        } else if (strcmp(s->kind, "dht22") == 0) {
            dht22Begin(*s);
        } else if (strcmp(s->kind, "fsr") == 0) {
            fsrBegin(*s);
        } else if (strcmp(s->kind, "ct") == 0) {
            ctBegin(*s);
        } else if (strcmp(s->kind, "ld2410c") == 0) {
            ld2410Begin(*s);
        }else {
            logError("SENS", "unknown kind \"%s\" for %s",
                     s->kind, s->virtual_id);
        }
    }
}

void sensorsTick() {
    for (uint8_t i = 0; i < sensorCount(); i++) {
        const hestia::SensorSlot* s = sensorAt(i);
        if (s == nullptr) continue;

        if (strcmp(s->kind, "pir") == 0) {
            pirTick(*s);
        } else if (strcmp(s->kind, "reed") == 0) {
            reedTick(*s);
        } else if (strcmp(s->kind, "dht22") == 0) {
            dht22Tick(*s);
        } else if (strcmp(s->kind, "fsr") == 0) {
            fsrTick(*s);
        } else if (strcmp(s->kind, "ct") == 0) {
            ctTick(*s);
        } else if (strcmp(s->kind, "ld2410c") == 0) {
            ld2410Begin(*s);
        }
    }
}

void sensorsResend() {
    for (uint8_t i = 0; i < sensorCount(); i++) {
        const hestia::SensorSlot* s = sensorAt(i);
        if (s == nullptr) continue;

        if (strcmp(s->kind, "pir") == 0) {
            pirResend(*s);
        } else if (strcmp(s->kind, "reed") == 0) {
            reedResend(*s);
        } else if (strcmp(s->kind, "dht22") == 0) {
            dht22Resend(*s);
        } else if (strcmp(s->kind, "fsr") == 0) {
            fsrResend(*s);
        } else if (strcmp(s->kind, "ct") == 0) {
            ctResend(*s);
        } else if (strcmp(s->kind, "ld2410c") == 0) {
            ld2410Begin(*s);
        }
    }
}

void sensorsAdcDebug(bool on) {
    fsrSetDebug(on);
    ctSetDebug(on);
    ld2410SetDebug(on);
}

void sensorsCalibrate() {
    ctCalibrate();
}

void sensorsStats() {
    ld2410Stats();
}