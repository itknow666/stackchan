#pragma once
namespace dania {
void start_agent_ble();
// Called only by the avatar update task while holding the LVGL lock.
void update_agent_ble();
bool remote_motion_active();
bool remote_face_active();
bool remote_rgb_active();
}
