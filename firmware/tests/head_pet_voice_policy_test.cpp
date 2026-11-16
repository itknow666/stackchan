#include "../main/hal/board/head_pet_voice_policy.h"
#include <cassert>
#include <cstdint>
int main()
{
    dania::HeadPetVoicePolicy p;
    assert(p.ready(0, true));
    assert(!p.ready(0, false));
    unsigned last = p.select(0, 9);
    assert(last == 9);
    assert(!p.ready(5999, true));
    assert(!p.ready(6000, false));
    for (uint32_t i = 1; i < 1000; ++i) {
        assert(p.ready(i * 6000, true));
        unsigned next = p.select(i * 6000, i);
        assert(next < 10 && next != last);
        last = next;
    }
    dania::HeadPetVoicePolicy wrap;
    wrap.select(UINT32_MAX - 1000, 0);
    assert(!wrap.ready(1000, true));
    assert(wrap.ready(6000, true));
    dania::HeadPetVoicePolicy small;
    unsigned old=small.select(0,999,3);
    for(uint32_t i=1;i<100;++i) {
        const auto next=small.select(i*6000,i,3);
        assert(next<3 && next!=old);
        old=next;
    }
    assert(small.select(600000,10,1)==0);
    assert(small.select(606000,10,0)==0);
}
