#include "time_sync.h"
#include "logging.h"
#include <ctime>
#include <Arduino.h>

static const uint32_t SYNC_THRESHOLD = 1700000000;   // 2023-11. 명세서 기준

static bool lastSynced = false;      // 이전에 동기화 되었는가를 나타내는 플래그

// 시연용 시계 오프셋. hestia/sim/clock 이 바꾼다. nowTs() 가 더해 돌려주므로
// sent_ts 를 쓰는 모든 곳이 따라온다 — 센서·상태·announce·ack 전부.
// ts_synced 와는 무관하다. 그건 NTP 동기화 여부이고, 엔진이 changed_at 을
// 믿을지 판단하는 데 쓴다.
static int32_t clockOffset_ = 0;

void timeSyncBegin() {
    configTime(0, 0, "pool.ntp.org", "time.google.com");
    logInfo("NTP", "requested (utc), waiting...");
}

bool isTimeSynced() {
    return (uint32_t)time(nullptr) > SYNC_THRESHOLD;
}

void timeSetOffset(int32_t seconds) { clockOffset_ = seconds; }

int32_t timeOffset() { return clockOffset_; }

uint32_t nowTs() {
    int64_t v = (int64_t)(uint32_t)time(nullptr) + clockOffset_;
    return v > 0 ? (uint32_t)v : 0;
}

bool timeSyncTick() {                 // 동기화 상태가 바뀐 순간에만 true
    bool now = isTimeSynced();

    if (now == lastSynced) {
        return false;
    }

    lastSynced = now;

    if (now) {
        time_t t = time(nullptr);
        char iso[32];
        strftime(iso, sizeof(iso), "%Y-%m-%d %H:%M:%S", gmtime(&t));
        logInfo("NTP", "synced ts=%lu utc=%s", (unsigned long)t, iso);
    } else {
        logWarn("NTP", "lost sync");
    }

    return true;
}