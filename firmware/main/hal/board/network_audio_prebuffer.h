#pragma once
#include <cstddef>
#include <cstdint>

namespace dania {
// Buffer compressed packets only; no additional PCM or DMA buffers are needed.
class NetworkAudioPrebuffer {
public:
    void begin() { enabled_ = true; started_ = false; }
    void finish() { enabled_ = false; started_ = false; }
    bool holding(std::size_t packets, uint32_t now_ms) {
        if (!enabled_) return false;
        if (packets == 0) return true;
        if (!started_) { first_ms_ = now_ms; started_ = true; }
        if (packets >= 4 || now_ms - first_ms_ >= 240) {
            enabled_ = false;
            return false;
        }
        return true;
    }
    uint32_t remaining_ms(uint32_t now_ms) const {
        if (!started_) return 240;
        const uint32_t elapsed = now_ms - first_ms_;
        return elapsed < 240 ? 240 - elapsed : 0;
    }
private:
    bool enabled_ = false;
    bool started_ = false;
    uint32_t first_ms_ = 0;
};
}
