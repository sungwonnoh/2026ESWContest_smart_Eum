#pragma once

#include <cstdint>
#include "payload.h"

// 빌드 플래그의 인벤토리 문자열을 파싱한다. setup() 에서 1회.
//   sensors: "vs-03:motion:pir:4,vs-05c:climate:dht22:15"
//   devices: "vd-10:display_node,vd-02:smart_light"
// 형식이 어긋난 항목은 건너뛰고 로그를 남긴다.
void inventoryBegin(const char* sensors_flag, const char* devices_flag);

uint8_t                     sensorCount();
const hestia::SensorSlot*   sensorAt(uint8_t i);

uint8_t     deviceCount();
const char* deviceVirtualIdAt(uint8_t i);
const char* deviceTypeAt(uint8_t i);
const hestia::DeviceSlot* deviceAt(uint8_t i);

// device_type 으로 virtual_id 를 찾는다. 없으면 nullptr
const char* findDeviceByType(const char* device_type);

// announce 의 emulates 배열을 채운다. 채운 개수를 반환
uint8_t buildEmulates(hestia::Emulated* out, uint8_t cap);