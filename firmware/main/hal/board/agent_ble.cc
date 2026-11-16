#include "agent_ble.h"
#include "agent_ble_queue.h"
#include "hal_bridge.h"
#include "sd_voice.h"
#include "sd_voice_manifest.h"
#include <application.h>
#include <hal/hal.h>
#include <stackchan/stackchan.h>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <ArduinoJson.hpp>
#include <hal/utils/bleprph/bleprph.h>
#include <cstdio>
#include <cstring>
#include <deque>
#include <map>
#include <mutex>

namespace dania {
static LatestBleCommands commands;
static RemoteControlLease motion_lease, face_lease, rgb_lease;
static std::mutex clip_mutex;
static uint32_t clip_request=0;
static std::map<uint32_t,std::string> clip_results;
static std::deque<uint32_t> clip_order;
static std::string clip_state="unavailable";
static bool clip_active=false;
static void clip_reply(uint32_t request,const std::string& state) {
    ArduinoJson::JsonDocument reply;
    reply["cmd"]="local_clip";
    reply["request_id"]=request;
    reply["state"]=state;
    std::string text;
    ArduinoJson::serializeJson(reply,text);
    stackchan_ble_notify_config(text.data(),static_cast<uint16_t>(text.size()));
}
static void clip_status(uint32_t request) {
    std::lock_guard<std::mutex> lock(clip_mutex);
    const auto found=clip_results.find(request);
    clip_reply(request,request==clip_request?clip_state:(found!=clip_results.end()?found->second:"unavailable"));
}
static uint32_t now_ms() { return static_cast<uint32_t>(esp_timer_get_time() / 1000); }
bool remote_motion_active() { return motion_lease.active(now_ms()); }
bool remote_face_active() { return face_lease.active(now_ms()); }
bool remote_rgb_active() { return rgb_lease.active(now_ms()); }

static void handle_volume_command(const char* payload)
{
    ArduinoJson::JsonDocument doc;
    if (ArduinoJson::deserializeJson(doc, payload) || !doc["request_id"].is<uint32_t>()) return;
    const uint32_t request = doc["request_id"].as<uint32_t>();
    if(doc["cmd"]=="getLocalClipStatus") {clip_status(request);return;}
    if(doc["cmd"]=="cancelLocalClip") {
        Application::GetInstance().Schedule([request]() {
            std::lock_guard<std::mutex> lock(clip_mutex);
            if(clip_active && clip_request==request) cancel_sd_voice();
        });
        return;
    }
    if(doc["cmd"]=="playLocalClip") {
        if(!doc["id"].is<ArduinoJson::JsonString>()) return;
        auto id=doc["id"].as<ArduinoJson::JsonString>();
        if(!id.c_str() || std::strlen(id.c_str())!=id.size() || !safe_voice_id({id.c_str(),id.size()}) || request==0) return;
        Application::GetInstance().Schedule([request,id=std::string(id.c_str(),id.size())]() {
            std::lock_guard<std::mutex> lock(clip_mutex);
            if(request==clip_request) {clip_reply(request,clip_state);return;}
            auto seen=clip_results.find(request);
            if(seen!=clip_results.end()) {clip_reply(request,seen->second);return;}
            if(clip_active) {clip_reply(request,"unavailable");return;}
            if(clip_request!=0) {
                clip_results[clip_request]=clip_state;
                clip_order.push_back(clip_request);
                if(clip_order.size()>64) {clip_results.erase(clip_order.front());clip_order.pop_front();}
            }
            clip_request=request;
            clip_state="unavailable";
            auto& app=Application::GetInstance();
            if(app.GetDeviceState()!=kDeviceStateIdle || !app.GetAudioService().IsPlaybackIdle() || !has_sd_voice(id)) {
                clip_reply(request,clip_state);return;
            }
            clip_active=true;
            clip_state="pending";
            const bool queued=request_sd_voice(id,[request](LocalVoiceEvent event,const char*) {
                Application::GetInstance().Schedule([request,event]() {
                    std::lock_guard<std::mutex> lock(clip_mutex);
                    if(clip_request!=request) return;
                    const bool accepted=clip_state=="accepted";
                    if(event==LocalVoiceEvent::Accepted) clip_state="accepted";
                    else if(event==LocalVoiceEvent::Completed) clip_state="completed";
                    else if(event==LocalVoiceEvent::Cancelled) clip_state="cancelled";
                    else clip_state=accepted?"failed":"unavailable";
                    if(event!=LocalVoiceEvent::Accepted) clip_active=false;
                    clip_reply(request,clip_state);
                });
            });
            if(!queued) {clip_active=false;clip_state="unavailable";clip_reply(request,clip_state);}
        });
        return;
    }
    if (doc["cmd"] == "getStorageInfo") {
        const auto storage = voice_storage_info();
        ArduinoJson::JsonDocument reply;
        reply["cmd"] = "storage";
        reply["request_id"] = request;
        reply["mounted"] = storage.mounted;
        reply["valid"] = storage.valid;
        reply["pack_id"] = storage.pack_id;
        reply["clips"] = storage.clips;
        reply["total_mb"] = storage.total_bytes / (1024 * 1024);
        reply["free_mb"] = storage.free_bytes / (1024 * 1024);
        reply["error"] = storage.error;
        std::string response;
        ArduinoJson::serializeJson(reply,response);
        stackchan_ble_notify_config(response.data(),static_cast<uint16_t>(response.size()));
        return;
    }
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
