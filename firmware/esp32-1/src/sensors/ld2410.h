#pragma once

#include "payload.h"

void ld2410Begin(const hestia::SensorSlot& slot);
void ld2410Tick(const hestia::SensorSlot& slot);
void ld2410Resend(const hestia::SensorSlot& slot);

void ld2410SetDebug(bool on);
void ld2410Stats();          // 콘솔: 프레임/에러 카운트