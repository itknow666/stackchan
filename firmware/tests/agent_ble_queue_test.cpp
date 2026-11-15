#include "../main/hal/board/agent_ble_queue.h"
#include <cassert>
#include <limits>

int main() {
    dania::LatestBleCommands q;
    std::string source = "first";
    assert(q.push(0, source.c_str()));
    source = "overwritten transport buffer";
    assert(q.take()[0] == "first");
    assert(q.push(0, "old"));
    assert(q.push(0, "latest"));
    assert(q.push(1, "face"));
    assert(q.push(2, "light"));
    assert(q.push(3, "volume"));
    auto batch = q.take();
    assert(batch[0] == "latest" && batch[1] == "face" && batch[2] == "light");
    assert(batch[3] == "volume");
    for (const auto& s : q.take()) assert(s.empty());
    assert(!q.push(4, "wrong channel"));
    assert(!q.push(0, nullptr));
    assert(!q.push(0, ""));
    assert(!q.push(0, std::string(2048, 'x').c_str()));
    assert(q.push(0, std::string(2047, 'x').c_str()));
    dania::RemoteControlLease lease;
    assert(!lease.active(0));
    lease.refresh(100);
    assert(lease.active(6099));
    assert(!lease.active(6100));
    lease.refresh(std::numeric_limits<uint32_t>::max() - 99);
    assert(lease.active(500));
    assert(!lease.active(6000));
}
