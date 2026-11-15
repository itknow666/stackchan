#include "../main/hal/board/network_audio_prebuffer.h"
#include <cassert>
#include <limits>

int main() {
    dania::NetworkAudioPrebuffer p;
    assert(!p.holding(1, 0)); // Local sounds have no prebuffer delay.
    p.begin();
    assert(p.holding(0, 0));
    assert(p.holding(1, 10000)); // Timeout begins with the first packet.
    assert(p.holding(3, 10180));
    assert(p.remaining_ms(10180) == 60);
    assert(!p.holding(4, 10190));
    assert(!p.holding(1, 10200)); // Do not repeatedly delay a running stream.
    p.begin();
    assert(p.holding(1, 20000));
    assert(p.holding(1, 20239));
    assert(!p.holding(1, 20240)); // A short or stalled stream cannot deadlock.
    assert(p.remaining_ms(20300) == 0);
    p.begin();
    assert(p.holding(1, 30000));
    p.finish();
    assert(!p.holding(1, 30001)); // End-of-stream flushes even one packet.
    p.begin();
    assert(p.holding(1, std::numeric_limits<uint32_t>::max() - 99));
    assert(p.holding(1, 139));
    assert(!p.holding(1, 140)); // Clock wraparound is safe.
    p.begin();
    p.finish();
    assert(!p.holding(0, 0)); // Cancellation also clears the pending delay.
}
