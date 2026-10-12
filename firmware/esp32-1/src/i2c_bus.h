#pragma once

#include <stdint.h>

// 여러 번 불러도 처음 한 번만 실제로 초기화한다
void i2cBegin(uint8_t sda, uint8_t scl);
bool i2cReady();