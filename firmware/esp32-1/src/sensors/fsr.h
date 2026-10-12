#pragma once

#include "payload.h"

void fsrBegin(const hestia::SensorSlot& slot);
void fsrTick(const hestia::SensorSlot& slot);
void fsrResend(const hestia::SensorSlot& slot);

void fsrSetDebug(bool on);   // 콘솔 캘리브레이션