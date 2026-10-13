#pragma once

#include "payload.h"

// 발행 함수를 주입받는다 (sensors.h 와 같은 패턴)
typedef bool (*DevicePublishFn)(const char* topic, uint8_t qos,
                                bool retain, const char* payload);

void devicesBegin(hestia::SeqCounter* seqs, DevicePublishFn publish);

// 토픽에서 추출한 virtual_id 로 cmd 를 라우팅한다.
// 내가 담당하지 않는 기기면 조용히 무시
void devicesHandleCmd(const char* virtual_id, const hestia::DeviceCmd& cmd);

// 내가 담당하는 모든 가전의 state 를 발행 (재연결·주기)
void devicesPublishAll();