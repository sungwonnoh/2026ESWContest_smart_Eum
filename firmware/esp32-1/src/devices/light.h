#pragma once

#include "payload.h"

void lightBegin(const hestia::DeviceSlot& slot);

// cmd 를 반영한다. 상태가 바뀌었으면 true (state 발행 필요)
bool lightApply(const hestia::DeviceSlot& slot, const hestia::DeviceCmd& cmd);

// 현재 상태를 state JSON 으로. 엔진에서 받은 값을 그대로 올린다
std::string lightStateJson(const hestia::DeviceSlot& slot,
                           uint32_t seq, uint32_t sent_ts);