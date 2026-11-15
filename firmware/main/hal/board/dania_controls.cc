#include "dania_controls.h"
#include "agent_ble.h"
#include <hal/hal.h>
#include <stackchan/stackchan.h>
#include <cmath>
#include <esp_log.h>

namespace dania {
void apply_pose(double yaw, double pitch, double roll)
{
    if (!std::isfinite(yaw) || !std::isfinite(pitch) || !std::isfinite(roll) ||
        std::abs(yaw) > 0.5 || std::abs(pitch) > 0.3 || roll != 0) return;
    const int y = static_cast<int>(std::lround(-yaw * 1800.0 / 3.141592653589793));
    const int p = static_cast<int>(std::lround(200.0 - pitch * 1800.0 / 3.141592653589793));
    LvglLockGuard lock;
    if (remote_motion_active()) return;
    GetStackChan().motion().moveWithSpeed(y, p, 150);
    ESP_LOGI("DANIA_POSE", "Applied bounded pose");
}
}
