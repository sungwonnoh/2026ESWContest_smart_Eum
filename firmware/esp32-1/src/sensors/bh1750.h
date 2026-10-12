#pragma once

#include "payload.h"

void bh1750Begin(const hestia::SensorSlot& slot);
void bh1750Tick(const hestia::SensorSlot& slot);
void bh1750Resend(const hestia::SensorSlot& slot);