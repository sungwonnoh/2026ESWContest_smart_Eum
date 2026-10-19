#pragma once

#include "payload.h"

void ldrBegin(const hestia::SensorSlot& slot);
void ldrTick(const hestia::SensorSlot& slot);
void ldrResend(const hestia::SensorSlot& slot);