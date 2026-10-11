#pragma once

#include "payload.h"

void dht22Begin(const hestia::SensorSlot& slot);
void dht22Tick(const hestia::SensorSlot& slot);
void dht22Resend(const hestia::SensorSlot& slot);