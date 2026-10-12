#include "ld2410.h"
#include "../sensors.h"
#include "../logging.h"
#include "../time_sync.h"
#include "ld2410_frame.h"
#include <Arduino.h>
#include <cstring>

static const uint8_t  TX_PIN          = 17;      // RX 는 slot.pin
static const uint32_t BAUD            = 256000;
static const uint32_t PERIOD_MS       = 300000;  // 5분
static const uint32_t ABSENT_HOLD_MS  = 3000;    // 부재 확정까지
static const uint32_t IDLE_RESET_MS   = 300;     // 바이트 끊기면 파서 리셋
static const uint32_t DEBUG_PERIOD_MS = 1000;
static const uint16_t DIST_DELTA_CM   = 50;
static const uint8_t  MAX_BYTES_TICK  = 128;     // 한 tick 당 읽을 한도

static hestia::Ld2410Parser parser_;

static const char* id_        = nullptr;
static bool        present_   = false;
static uint16_t    dist_      = 0;
static uint8_t     energy_    = 0;
static bool        valid_     = false;
static uint32_t    lastByte_  = 0;
static uint32_t    lastPub_   = 0;
static uint32_t    absentFrom_ = 0;    // state==0 이 시작된 시각
static bool        debug_     = false;
static uint32_t    lastDbg_   = 0;

static void publishNow(const hestia::SensorSlot& slot) {
    uint32_t seq = 0;
    if (!sensorNextSeq(slot, seq)) return;

    sensorPublish(slot,
        hestia::buildPresence(slot.virtual_id, seq, nowTs(),
                              present_, energy_, dist_));
    lastPub_ = millis();
}

static void onFrame(const hestia::SensorSlot& slot,
                    const hestia::Ld2410Frame& f, uint32_t t) {
    bool raw = (f.state != 0);

    // 부재 판정에만 지연
    bool next = present_;
    if (raw) {
        next        = true;
        absentFrom_ = 0;
    } else {
        if (absentFrom_ == 0) {
            absentFrom_ = t;
        } else if (t - absentFrom_ >= ABSENT_HOLD_MS) {
            next = false;
        }
    }

    uint8_t  energy = (f.moving_energy > f.static_energy)
                        ? f.moving_energy : f.static_energy;
    uint16_t dist   = f.detect_dist_cm;

    uint16_t diff = (dist > dist_) ? (dist - dist_) : (dist_ - dist);

    bool changed = !valid_
                || next != present_
                || (next && diff >= DIST_DELTA_CM);
    bool due     = (t - lastPub_ >= PERIOD_MS);

    present_ = next;
    dist_    = dist;
    energy_  = energy;
    valid_   = true;

    if (changed || due) {
        publishNow(slot);
    }
}

void ld2410Begin(const hestia::SensorSlot& slot) {
    if (id_ != nullptr) {
        logError("LD", "only one ld2410 per node, dropped %s",
                 slot.virtual_id);
        return;
    }

    Serial2.begin(BAUD, SERIAL_8N1, slot.pin, TX_PIN);
    id_       = slot.virtual_id;
    lastByte_ = millis();

    logInfo("LD", "%s rx=%u tx=%u @%u",
            slot.virtual_id, slot.pin, TX_PIN, BAUD);
}

void ld2410Tick(const hestia::SensorSlot& slot) {
    if (id_ == nullptr) return;

    uint32_t t = millis();

    // 흐름이 끊겼으면 파서를 깨끗이
    if (t - lastByte_ > IDLE_RESET_MS) {
        parser_.reset();
        lastByte_ = t;
    }

    uint8_t budget = MAX_BYTES_TICK;
    while (Serial2.available() && budget-- > 0) {
        uint8_t b = (uint8_t)Serial2.read();
        lastByte_ = t;

        hestia::Ld2410Frame f;
        if (parser_.feed(b, f)) {
            onFrame(slot, f, t);
        }
    }

    if (debug_ && t - lastDbg_ >= DEBUG_PERIOD_MS) {
        lastDbg_ = t;
        logInfo("LD", "%s present=%d dist=%ucm energy=%u (f=%u e=%u)",
                id_, present_ ? 1 : 0, dist_, energy_,
                parser_.frameCount(), parser_.errorCount());
    }
}

void ld2410Resend(const hestia::SensorSlot& slot) {
    if (id_ == nullptr) return;
    if (!valid_) {
        logWarn("LD", "%s resend skipped, no frame yet", slot.virtual_id);
        return;
    }
    publishNow(slot);
}

void ld2410SetDebug(bool on) {
    debug_   = on;
    lastDbg_ = 0;
    logInfo("LD", "debug %s", on ? "on" : "off");
}

void ld2410Stats() {
    if (id_ == nullptr) {
        logWarn("LD", "not configured");
        return;
    }
    uint32_t f = parser_.frameCount();
    uint32_t e = parser_.errorCount();
    logInfo("LD", "%s frames=%u errors=%u (%.1f%% bad)",
            id_, f, e, (f + e > 0) ? (100.0f * e / (f + e)) : 0.0f);
}