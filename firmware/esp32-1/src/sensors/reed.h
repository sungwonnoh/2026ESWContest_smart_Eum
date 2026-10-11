#pragma once

#include "payload.h"

void reedBegin(const hestia::SensorSlot& slot);
void reedTick(const hestia::SensorSlot& slot);
void reedResend(const hestia::SensorSlot& slot);   // 재연결 시 현재 상태 강제 발행