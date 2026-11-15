#include "agent_ble.h"
#include "agent_ble_queue.h"
#include "hal_bridge.h"
#include <hal/hal.h>
#include <stackchan/stackchan.h>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <ArduinoJson.hpp>
#include <hal/utils/bleprph/bleprph.h>
#include <cstdio>

namespace dania {
static LatestBleCommands commands;
static RemoteControlLease motion_lease, face_lease, rgb_lease;
static uint32_t now_ms() { return static_cast<uint32_t>(esp_timer_get_time() / 1000); }
bool remote_motion_active() { return motion_lease.active(now_ms()); }
bool remote_face_active() { return face_lease.active(now_ms()); }
bool remote_rgb_active() { return rgb_lease.active(now_ms()); }

static void handle_volume_command(const char* payload)
{
    ArduinoJson::JsonDocument doc;
    if (ArduinoJson::deserializeJson(doc, payload) || !doc["request_id"].is<uint32_t>()) return;
    const uint32_t request = doc["request_id"].as<uint32_t>();
    if (doc["cmd"] == "setVolume") {
        if (!doc["value"].is<int>()) return;
        const int volume = doc["value"].as<int>();
        if (volume < 0 || volume > 100) return;
        if (GetHAL().getSpeakerVolume() != volume) GetHAL().setSpeakerVolume(volume, true);
        ESP_LOGI("DANIA_BLE", "Applied volume=%d in Agent", volume);
    } else if (doc["cmd"] != "getVolume") {
        return;
    }
    char response[96];
    const int length = std::snprintf(response, sizeof(response),
        "{\"cmd\":\"volume\",\"value\":%u,\"request_id\":%lu}",
        static_cast<unsigned>(GetHAL().getSpeakerVolume()), static_cast<unsigned long>(request));
    if (length > 0 && length < static_cast<int>(sizeof(response))) {
        stackchan_ble_notify_config(response, static_cast<uint16_t>(length));
    }
}

void start_agent_ble()
{
    static bool started = false;
    if (started) return;
    started = true;
    auto enqueue = [](size_t channel, const char* payload) {
        if (hal_bridge::is_xiaozhi_mode()) commands.push(channel, payload);
    };
    GetHAL().onBleMotionData.connect([enqueue](const char* p) { enqueue(0, p); });
    GetHAL().onBleAvatarData.connect([enqueue](const char* p) { enqueue(1, p); });
    GetHAL().onBleRgbData.connect([enqueue](const char* p) { enqueue(2, p); });
    GetHAL().onBleConfigData.connect([enqueue](const char* p) { enqueue(3, p); });
    const auto before = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    GetHAL().startBleServer();
    ESP_LOGI("DANIA_BLE", "Agent BLE started; internal heap before=%u after=%u",
             static_cast<unsigned>(before),
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)));
}

void update_agent_ble()
{
    auto pending = commands.take();
    auto& robot = GetStackChan();
    const auto now = now_ms();
    if (!pending[0].empty()) {
        motion_lease.refresh(now);
        robot.motion().setAutoAngleSyncEnabled(true);
        robot.updateMotionFromJson(pending[0].c_str());
        ESP_LOGI("DANIA_BLE", "Applied motion command in Agent");
    }
    if (!pending[1].empty() && robot.hasAvatar()) {
        face_lease.refresh(now);
        robot.updateAvatarFromJson(pending[1].c_str());
        ESP_LOGI("DANIA_BLE", "Applied face command in Agent");
    }
    if (!pending[2].empty()) {
        rgb_lease.refresh(now);
        robot.updateNeonLightFromJson(pending[2].c_str());
        ESP_LOGI("DANIA_BLE", "Applied light command in Agent");
    }
    if (!pending[3].empty()) handle_volume_command(pending[3].c_str());
}
}
