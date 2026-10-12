#pragma once

#include "payload.h"

// slot.kind 가 "dht11" 또는 "dht22"
void dhtBegin(const hestia::SensorSlot& slot);
void dhtTick(const hestia::SensorSlot& slot);
void dhtResend(const hestia::SensorSlot& slot);