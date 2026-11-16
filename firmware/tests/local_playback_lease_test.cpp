#include "../main/hal/board/local_playback_lease.h"
#include <cassert>
#include <cstdint>
int main() {
    dania::LocalPlaybackLease lease;
    uint32_t generation=0;
    assert(lease.begin(generation,0,false,false,false)==0);
    assert(lease.begin(generation,0,true,true,false)==0);
    assert(lease.begin(generation,0,true,false,true)==0);
    auto token=lease.begin(generation,0,true,false,false);
    assert(token!=0 && lease.current(generation,token,false));
    assert(!lease.can_notify_drained(true,false));
    assert(!lease.current(generation,token,true));
    assert(lease.begin(generation,generation,true,false,false)==0);
    // ResetDecoder changes the generation while the producer waits for space.
    ++generation;
    lease.invalidate();
    assert(lease.can_notify_drained(true,false));
    assert(!lease.can_notify_drained(false,false));
    assert(!lease.can_notify_drained(true,true));
    assert(!lease.current(generation,token,false));
    assert(lease.begin(generation,token,true,false,false)==0);
    auto next=lease.begin(generation,generation,true,false,false);
    assert(next!=token && !lease.current(generation,token,false));
    assert(lease.current(generation,next,false));
    lease.invalidate();
    assert(!lease.current(generation,next,false));
    generation=UINT32_MAX;
    assert(lease.begin(generation,UINT32_MAX,true,false,false)==1);
    assert(!lease.current(generation,0,false));
}
