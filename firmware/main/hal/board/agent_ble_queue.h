#pragma once
#include <array>
#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>

namespace dania {
// Own each payload: the BLE transport reuses its receive buffers immediately.
// Keep only the latest command per channel so a fast slider cannot grow a queue.
class LatestBleCommands {
public:
    static constexpr size_t MaxPayload = 2047;
    static constexpr size_t Channels = 4;
    bool push(size_t channel, const char* data) {
        if (channel >= slots_.size() || !data) return false;
        size_t length = 0;
        while (length <= MaxPayload && data[length]) ++length;
        if (length == 0 || length > MaxPayload) return false;
        std::lock_guard<std::mutex> lock(mutex_);
        slots_[channel].assign(data, length);
        return true;
    }
    std::array<std::string, Channels> take() {
        std::array<std::string, Channels> result;
        std::lock_guard<std::mutex> lock(mutex_);
        result.swap(slots_);
        return result;
    }
private:
    std::mutex mutex_;
    std::array<std::string, Channels> slots_;
};

class RemoteControlLease {
public:
    void refresh(uint32_t now) { last_.store(now); seen_.store(true); }
    bool active(uint32_t now) const {
        return seen_.load() && static_cast<uint32_t>(now - last_.load()) < 6000;
    }
private:
    std::atomic<uint32_t> last_{0};
    std::atomic<bool> seen_{false};
};
}
