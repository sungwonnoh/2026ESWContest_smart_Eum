#pragma once

#include <stdint.h>
#include <stddef.h>

namespace hestia {

// 파싱된 한 프레임
struct Ld2410Frame {
    uint8_t  state;             // 0=없음 1=움직임 2=정지 3=둘다
    uint16_t moving_dist_cm;
    uint8_t  moving_energy;     // 0~100
    uint16_t static_dist_cm;
    uint8_t  static_energy;
    uint16_t detect_dist_cm;
};

// 바이트를 하나씩 먹고, 프레임이 완성되면 true
class Ld2410Parser {
public:
    Ld2410Parser();

    // 바이트 하나 투입. 프레임 완성 시 out 에 채우고 true
    bool feed(uint8_t b, Ld2410Frame& out);

    void reset();

    uint32_t frameCount() const { return frames_; }
    uint32_t errorCount() const { return errors_; }

private:
    enum class St : uint8_t {
        H0, H1, H2, H3,         // F4 F3 F2 F1
        LEN_LO, LEN_HI,
        BODY,
        T0, T1, T2, T3          // F8 F7 F6 F5
    };

    static const uint8_t MAX_BODY = 64;

    St       st_;
    uint16_t len_;
    uint8_t  body_[MAX_BODY];
    uint8_t  bodyLen_;
    uint32_t frames_;
    uint32_t errors_;

    void fail();
    bool buildFrame(Ld2410Frame& out) const;
};

}  // namespace hestia