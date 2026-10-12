#pragma once

#include "payload.h"

void ctBegin(const hestia::SensorSlot& slot);
void ctTick(const hestia::SensorSlot& slot);
void ctResend(const hestia::SensorSlot& slot);

void ctSetDebug(bool on);
void ctCalibrate();          // 영점 재측정