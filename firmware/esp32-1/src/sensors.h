#pragma once

#include <cstdint>
#include <string>
#include "payload.h"

// 드라이버가 완성한 페이로드를 발행한다.
// 토픽·QoS·retained 는 slot.type 에서 결정한다.
typedef bool (*SensorPublishFn)(const char* topic, uint8_t qos,
                                bool retain, const char* payload);

// setup() 에서 1회. 인벤토리를 돌며 각 드라이버를 초기화한다.
void sensorsBegin(hestia::SeqCounter* seqs, SensorPublishFn publish);

// loop() 에서 매번.
void sensorsTick();
void sensorsResend();   // MQTT 재연결 직후

// ── 드라이버가 쓰는 헬퍼 ─────────────────────
// 이 slot 의 다음 seq. 슬롯이 가득 차면 false
bool sensorNextSeq(const hestia::SensorSlot& slot, uint32_t& out_seq);

// 토픽·QoS·retained 를 붙여 발행한다
bool sensorPublish(const hestia::SensorSlot& slot, const std::string& payload);

void sensorsAdcDebug(bool on);

void sensorsCalibrate();

void sensorsStats();