#include "ld2410_frame.h"

namespace hestia {

Ld2410Parser::Ld2410Parser() {
    frames_ = 0;
    errors_ = 0;
    reset();
}

void Ld2410Parser::reset() {
    st_      = St::H0;
    len_     = 0;
    bodyLen_ = 0;
}

void Ld2410Parser::fail() {
    errors_++;
    reset();
}

// body: 02 AA state mv_lo mv_hi mv_en st_lo st_hi st_en dt_lo dt_hi 55 00
bool Ld2410Parser::buildFrame(Ld2410Frame& out) const {
    if (bodyLen_ < 13)          return false;
    if (body_[0] != 0x02)       return false;   // 기본 모드
    if (body_[1] != 0xAA)       return false;   // 헤드
    if (body_[11] != 0x55)      return false;   // 테일
    if (body_[12] != 0x00)      return false;   // 체크

    out.state          = body_[2];
    out.moving_dist_cm = (uint16_t)body_[3] | ((uint16_t)body_[4] << 8);
    out.moving_energy  = body_[5];
    out.static_dist_cm = (uint16_t)body_[6] | ((uint16_t)body_[7] << 8);
    out.static_energy  = body_[8];
    out.detect_dist_cm = (uint16_t)body_[9] | ((uint16_t)body_[10] << 8);
    return true;
}

bool Ld2410Parser::feed(uint8_t b, Ld2410Frame& out) {
    switch (st_) {
    case St::H0:
        st_ = (b == 0xF4) ? St::H1 : St::H0;
        return false;

    case St::H1:
        if (b == 0xF3)      st_ = St::H2;
        else if (b == 0xF4) st_ = St::H1;      // F4 F4 ... 재시작
        else                st_ = St::H0;
        return false;

    case St::H2:
        if (b == 0xF2)      st_ = St::H3;
        else if (b == 0xF4) st_ = St::H1;
        else                st_ = St::H0;
        return false;

    case St::H3:
        if (b == 0xF1)      st_ = St::LEN_LO;
        else if (b == 0xF4) st_ = St::H1;
        else                st_ = St::H0;
        return false;

    case St::LEN_LO:
        len_ = b;
        st_  = St::LEN_HI;
        return false;

    case St::LEN_HI:
        len_ |= ((uint16_t)b << 8);
        if (len_ == 0 || len_ > MAX_BODY) {
            fail();                            // 말이 안 되는 길이
            return false;
        }
        bodyLen_ = 0;
        st_      = St::BODY;
        return false;

    case St::BODY:
        body_[bodyLen_++] = b;
        if (bodyLen_ >= len_) st_ = St::T0;
        return false;

    case St::T0:
        if (b != 0xF8) { fail(); return false; }
        st_ = St::T1;
        return false;

    case St::T1:
        if (b != 0xF7) { fail(); return false; }
        st_ = St::T2;
        return false;

    case St::T2:
        if (b != 0xF6) { fail(); return false; }
        st_ = St::T3;
        return false;

    case St::T3:
        if (b != 0xF5) { fail(); return false; }
        {
            bool ok = buildFrame(out);
            reset();
            if (ok) { frames_++; return true; }
            errors_++;
            return false;
        }
    }

    fail();
    return false;
}

}  // namespace hestia