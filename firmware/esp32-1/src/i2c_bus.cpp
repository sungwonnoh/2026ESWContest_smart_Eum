#include "i2c_bus.h"
#include "logging.h"
#include <Wire.h>

static bool    started_ = false;
static uint8_t sda_     = 255;
static uint8_t scl_     = 255;

void i2cBegin(uint8_t sda, uint8_t scl) {
    if (started_) {
        if (sda != sda_ || scl != scl_) {
            logWarn("I2C", "already on %u/%u, ignoring %u/%u",
                    sda_, scl_, sda, scl);
        }
        return;
    }

    Wire.begin(sda, scl);
    Wire.setClock(100000);
    started_ = true;
    sda_     = sda;
    scl_     = scl;

    logInfo("I2C", "bus on sda=%u scl=%u", sda, scl);
}

bool i2cReady() {
    return started_;
}