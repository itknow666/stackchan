#pragma once
#include <cstdint>

namespace dania {
// AudioService owns the mutex and its shared decoder generation. The file
// producer must keep this one token across every packet, including after waits.
class LocalPlaybackLease {
public:
    uint32_t begin(uint32_t& generation, uint32_t expected, bool drained,
                   bool stopped, bool recording) {
        if (active_ || expected != generation || !drained || stopped || recording) return 0;
        if (++generation == 0) ++generation;
        active_ = true;
        return generation;
    }
    bool current(uint32_t generation, uint32_t token, bool stopped) const {
        return active_ && !stopped && token != 0 && token == generation;
    }
    bool active() const { return active_; }
    bool can_notify_drained(bool drained, bool already_notified) const {
        return !active_ && drained && !already_notified;
    }
    void invalidate() { active_ = false; }
private:
    bool active_ = false;
};
}
