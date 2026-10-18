#pragma once

#include <cstdint>
#include "payload.h"

// ── 공용 디바운스 ────────────────────────────
// 센서 슬롯이 아닌 버튼(ack)도 같은 것을 쓴다.
struct ButtonPin {
    uint8_t  pin       = 255;
    bool     stable    = true;     // INPUT_PULLUP — 안 눌리면 HIGH
    bool     lastRaw   = true;
    uint32_t changedMs = 0;
};

void buttonAttach(ButtonPin& b, uint8_t pin);

// 디바운스를 통과한 '방금 눌림'(하강 엣지)에만 한 번 true
bool buttonPressed(ButtonPin& b);

// ── 센서를 흉내 내는 토글 버튼 ───────────────
// 인벤토리:
//   vs-06:power:button:13      인덕션 — 켜고 끄기
//   vs-02:bed:button:5         압력 패드 대역 — 눌림/해제
//
// 실물이 오면 kind 만 바꾼다 (button → ct, button → fsr).
// 핀은 그대로 두되 FSR 은 ADC1(32~39)이어야 한다 — ADC2 는 Wi-Fi 가
// 켜지면 못 읽는다.
void buttonBegin(const hestia::SensorSlot& slot);
void buttonTick(const hestia::SensorSlot& slot);
void buttonResend(const hestia::SensorSlot& slot);