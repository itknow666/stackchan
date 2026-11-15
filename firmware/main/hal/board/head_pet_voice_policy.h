#pragma once
#include <cstdint>

namespace dania {
// Called only on the application task; unsigned subtraction tolerates wraparound.
class HeadPetVoicePolicy {
public:
    bool ready(uint32_t now, bool idle) const
    {
        return idle && (!_played || static_cast<uint32_t>(now - _last_tick) >= 6000);
    }
    unsigned select(uint32_t now, uint32_t random)
    {
        unsigned index = _played ? random % 9 : random % 10;
        if (_played && index >= _last_index) ++index;
        _played = true;
        _last_tick = now;
        _last_index = index;
        return index;
    }
private:
    bool _played = false;
    uint32_t _last_tick = 0;
    unsigned _last_index = 0;
};
}
