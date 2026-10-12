#pragma once

#include "payload.h"

void pirBegin(const hestia::SensorSlot& slot);
void pirTick(const hestia::SensorSlot& slot);
void pirResend(const hestia::SensorSlot& slot);