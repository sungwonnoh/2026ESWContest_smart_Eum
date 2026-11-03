#include "display.h"
#include "i2c_bus.h"
#include "logging.h"
#include <Arduino.h>
#include <Wire.h>
#include <U8g2lib.h>
#include <cstring>

// 거실 TV 배너 대행. 알림 하나를 제목 한 줄 + 본문 세 줄로 띄운다.
// 128x64 에 16px 한글 폰트라 한 줄 8자, 네 줄이 전부다 —
// notify.py 의 "화면 본문 (8글자 4줄)" 이 이 화면을 가리킨다.
//
// 화면이 없어도 동작은 같다. ready_ 가 false 면 그리기만 건너뛰고
// displayCurrent() 는 그대로라 device state 발행에 영향이 없다.

static const uint8_t SDA_PIN   = 21;
static const uint8_t SCL_PIN   = 22;
static const uint8_t OLED_ADDR = 0x3C;
static const uint8_t LINE_H    = 16;
static const uint32_t HOLD_MS = 60000;

static U8G2_SSD1306_128X64_NONAME_F_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE);

static bool ready_        = false;
static bool urgent_       = false;
static uint32_t shown_at_ = 0;
static char title_[48]    = {0};
static char current_[128] = {0};

static void render();

static bool probe(uint8_t addr) {
    Wire.beginTransmission(addr);
    return Wire.endTransmission() == 0;
}

void displayBegin() {
    i2cBegin(SDA_PIN, SCL_PIN);

    if (!probe(OLED_ADDR)) {
        logWarn("DISP", "no panel at 0x%02X — serial only", OLED_ADDR);
        return;
    }

    oled.setI2CAddress(OLED_ADDR << 1);
    oled.begin();
    oled.setFont(u8g2_font_unifont_t_korean2);
    oled.setFontPosTop();
    ready_ = true;

    logInfo("DISP", "SSD1306 128x64 sda=%u scl=%u", SDA_PIN, SCL_PIN);
    render();
}

// \n 으로 끊어 최대 max 줄. 넘치는 줄은 버린다 — 자르는 것보다
// 안 보여주는 쪽이 낫다.
static void drawLines(const char* text, uint8_t y, uint8_t max) {
    char        buf[48];
    const char* p = text;
    uint8_t     n = 0;

    while (*p != '\0' && n < max) {
        const char* nl  = strchr(p, '\n');
        size_t      len = (nl != nullptr) ? (size_t)(nl - p) : strlen(p);
        if (len >= sizeof(buf)) len = sizeof(buf) - 1;
        memcpy(buf, p, len);
        buf[len] = '\0';

        oled.drawUTF8(0, y, buf);
        y += LINE_H;
        n++;

        if (nl == nullptr) break;
        p = nl + 1;
    }
}

static void render() {
    if (!ready_) return;

    oled.clearBuffer();

    if (current_[0] == '\0' && title_[0] == '\0') {
        oled.drawUTF8(34, 24, "HESTIA");
        oled.sendBuffer();
        return;
    }

    uint8_t y    = 0;
    uint8_t body = 4;

    if (title_[0] != '\0') {
        if (urgent_) {
            // 급한 것은 제목 줄을 반전한다. 멀리서도 갈린다.
            oled.drawBox(0, 0, 128, LINE_H);
            oled.setDrawColor(0);
            oled.drawUTF8(2, 0, title_);
            oled.setDrawColor(1);
        } else {
            oled.drawUTF8(0, 0, title_);
            oled.drawHLine(0, LINE_H - 1, 128);
        }
        y    = LINE_H;
        body = 3;
    }

    drawLines(current_, y, body);
    oled.sendBuffer();
}

void displayTick() {
    if (shown_at_ == 0) return;
    if (millis() - shown_at_ < HOLD_MS) return;

    shown_at_   = 0;
    current_[0] = '\0';
    title_[0]   = '\0';
    urgent_     = false;

    logInfo("DISP", "auto-clear after %lus", (unsigned long)(HOLD_MS / 1000));
    render();
}

void displayShow(const char* title, const char* text, const char* priority) {
    strncpy(current_, text != nullptr ? text : "", sizeof(current_) - 1);
    current_[sizeof(current_) - 1] = '\0';
    strncpy(title_, title != nullptr ? title : "", sizeof(title_) - 1);
    title_[sizeof(title_) - 1] = '\0';

    urgent_ = (strcmp(priority, "health") == 0 || strcmp(priority, "high") == 0);
    shown_at_ = millis();

    if (strcmp(priority, "health") == 0) {
        logError("DISP", "[%s] %s | %s", priority, title, text);
    } else if (strcmp(priority, "high") == 0) {
        logWarn("DISP", "[%s] %s | %s", priority, title, text);
    } else {
        logInfo("DISP", "[%s] %s | %s", priority, title, text);
    }

    render();
}

void displayClear() {
    if (current_[0] != '\0') {
        logInfo("DISP", "cleared");
    }
    current_[0] = '\0';
    title_[0]   = '\0';
    urgent_     = false;
    shown_at_   = 0;
    render();
}

const char* displayCurrent() {
    return current_;
}