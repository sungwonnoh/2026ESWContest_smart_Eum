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

void devicesBegin(hestia::SeqCounter* seqs, DevicePublishFn publish);
void devicesHandleCmd(const char* virtual_id, const hestia::DeviceCmd& cmd);
void devicesPublishAll();

// loop() 에서 매번. 버튼이 달린 가전(냉장고·정수기)을 읽는다.
void devicesTick();

// ── 드라이버가 쓰는 헬퍼 (sensors.h 와 같은 패턴) ──
bool deviceNextSeq(const hestia::DeviceSlot& slot, uint32_t& out_seq);
bool devicePublishState(const hestia::DeviceSlot& slot, const std::string& payload);
bool devicePublishEvent(const hestia::DeviceSlot& slot, const std::string& payload);