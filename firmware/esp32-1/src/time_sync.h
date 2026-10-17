#pragma once

#include <cstdint>

// Wi-Fi 연결 직후에 호출, NTP 요청만 걸고 즉시 반환
void timeSyncBegin();

// loop()에서 매번 호출, false→true로 바뀐 그 호출에서만 true를 반환
bool timeSyncTick();

// 현재 시간 동기화되어 있는지
bool isTimeSynced();

// sent_ts에 쓸 값, 동기화 전에는 부팅 후 경과초에 가까운 작은 값이 나옴
uint32_t nowTs();

// 시연용 시계 오프셋. hestia/sim/clock 이 바꾼다.
// nowTs() 가 이걸 더해 돌려주므로 sent_ts 를 쓰는 모든 곳이 따라온다.
// ts_synced 와는 무관하다 — 그건 NTP 동기화 여부다.
void    timeSetOffset(int32_t seconds);
int32_t timeOffset();