#include "ld2410.h"
#include "../sensors.h"
#include "../logging.h"
#include "../time_sync.h"
#include "ld2410_frame.h"
#include <Arduino.h>
#include <cstring>

static const uint8_t  TX_PIN          = 17;      // RX 는 slot.pin
static const uint32_t BAUD            = 256000;
static const uint32_t PERIOD_MS       = 20000;  // 5분
static const uint32_t ABSENT_HOLD_MS  = 0;    // 부재 확정까지
static const uint32_t STILL_HOLD_MS   = 2000;    // 정지↔이동 전환 디바운스
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

// 이동 에너지, 정지 에너지 구분
static uint8_t  mov_         = 0;
static uint8_t  sta_         = 0;
static uint8_t  target_      = 0;
static bool     still_     = false;
static uint32_t stillFrom_ = 0;

// 거리 변동폭
static const uint8_t  SPREAD_SLOTS   = 10;     // 1초 버킷 10개 = 10초 창
static const uint32_t SPREAD_TICK_MS = 1000;
static uint16_t spMin_[SPREAD_SLOTS];
static uint16_t spMax_[SPREAD_SLOTS];
static uint8_t  spCount_ = 0;
static uint8_t  spHead_  = 0;
static uint32_t spSince_ = 0;


static const char* targetStr(uint8_t s) {
    switch (s) {
        case 0: return "NONE";
        case 1: return "MOVING";
        case 2: return "STATIC";
        case 3: return "BOTH";
    }
    return "?";
}

static void noteDistance(uint16_t dist, uint32_t t) {
    if (spCount_ == 0) {                                   // 첫 샘플
        spSince_ = t;
        spMin_[spHead_] = dist;
        spMax_[spHead_] = dist;
        spCount_ = 1;
        return;
    }
    if (t - spSince_ >= SPREAD_TICK_MS) {                  // 다음 버킷
        spSince_ = t;
        spHead_  = (spHead_ + 1) % SPREAD_SLOTS;
        spMin_[spHead_] = dist;
        spMax_[spHead_] = dist;
        if (spCount_ < SPREAD_SLOTS) spCount_++;
        return;
    }
    if (dist < spMin_[spHead_]) spMin_[spHead_] = dist;     // 현재 버킷 갱신
    if (dist > spMax_[spHead_]) spMax_[spHead_] = dist;
}

static uint16_t distSpread() {
    if (spCount_ == 0) return 0;
    uint16_t lo = spMin_[0], hi = spMax_[0];
    for (uint8_t i = 1; i < spCount_; i++) {
        if (spMin_[i] < lo) lo = spMin_[i];
        if (spMax_[i] > hi) hi = spMax_[i];
    }
    return hi - lo;
}

static void publishNow(const hestia::SensorSlot& slot) {
    uint32_t seq = 0;
    if (!sensorNextSeq(slot, seq)) return;

    hestia::PresenceReading r;
    r.present        = present_;
    r.energy         = energy_;
    r.distance_cm    = dist_;
    r.has_detail     = true;
    r.moving_energy  = mov_;
    r.static_energy  = sta_;
    r.target         = target_;
    r.dist_spread_cm = distSpread();

    sensorPublish(slot, hestia::buildPresence(slot.virtual_id, seq, nowTs(), r));
    lastPub_ = millis();
}

static void onFrame(const hestia::SensorSlot& slot,
                    const hestia::Ld2410Frame& f, uint32_t t) {
    bool raw = (f.state != 0);

    // 부재 판정 — ABSENT_HOLD_MS=0 이면 즉시
    bool next = present_;
    if (raw) {
        next        = true;
        absentFrom_ = 0;
    } else {
        if (absentFrom_ == 0) absentFrom_ = t;
        if (t - absentFrom_ >= ABSENT_HOLD_MS) next = false;
    }

    // 정지 ↔ 이동 전환 — 2초 유지돼야 인정한다
    bool rawStill  = (f.state == 2);
    bool nextStill = still_;
    if (rawStill != still_) {
        if (stillFrom_ == 0) {
            stillFrom_ = t;
        } else if (t - stillFrom_ >= STILL_HOLD_MS) {
            nextStill  = rawStill;
            stillFrom_ = 0;
        }
    } else {
        stillFrom_ = 0;
    }

    uint8_t  energy = (f.moving_energy > f.static_energy)
                        ? f.moving_energy : f.static_energy;
    uint16_t dist   = next ? f.detect_dist_cm : 0;   // 부재면 거리를 남기지 않는다

    if (!next) {
        spCount_ = 0;
        spHead_  = 0;
    } else if (dist > 0) {
        noteDistance(dist, t);          // 0 은 "거리 모름" — 버린다
    }

    uint16_t diff = (dist > dist_) ? (dist - dist_) : (dist_ - dist);

    bool changed = !valid_
                || next != present_
                || nextStill != still_          // 앉는 순간 · 일어나는 순간
                || (next && diff >= DIST_DELTA_CM);
    bool due     = (t - lastPub_ >= PERIOD_MS);

    present_ = next;
    still_   = nextStill;
    dist_    = dist;
    energy_  = energy;
    mov_     = f.moving_energy;
    sta_     = f.static_energy;
    target_  = f.state;
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
        logInfo("LD", "%s p=%d d=%ucm sp=%u | mov=%u sta=%u | %s | f=%u err=%u",
                id_, present_ ? 1 : 0, dist_, distSpread(),
                mov_, sta_, targetStr(target_),
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