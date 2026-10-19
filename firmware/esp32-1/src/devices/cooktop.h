#pragma once
#include "payload.h"

void cooktopBegin(const hestia::DeviceSlot& slot);
void cooktopTick(const hestia::DeviceSlot& slot);
std::string cooktopStateJson(const hestia::DeviceSlot& slot,
                             uint32_t seq, uint32_t sent_ts);