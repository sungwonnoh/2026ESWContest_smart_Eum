#pragma once
#include "payload.h"

void fridgeBegin(const hestia::DeviceSlot& slot);
void fridgeTick(const hestia::DeviceSlot& slot);
std::string fridgeStateJson(const hestia::DeviceSlot& slot,
                            uint32_t seq, uint32_t sent_ts);