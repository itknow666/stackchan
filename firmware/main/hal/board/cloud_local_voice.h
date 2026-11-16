#pragma once
#include <cJSON.h>
#include <cstdint>
#include <functional>
#include <string>

namespace dania {
using CloudLocalReply = std::function<void(const std::string&, const char*)>;
// Capability snapshot is read-only and can be built on the protocol task.
cJSON* local_audio_capabilities();
// These lifecycle/dispatch methods run only on Application's owning task.
void local_audio_connection(bool connected);
uint64_t local_audio_generation();
void request_cloud_local_voice(std::string request, std::string pack, std::string clip,
                               int timeout_ms, CloudLocalReply reply);
void cancel_cloud_local_voice(const std::string& request = {});
}
