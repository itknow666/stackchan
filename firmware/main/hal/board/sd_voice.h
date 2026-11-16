#pragma once
#include <cstdint>
#include <functional>
#include <string>
#include <string_view>
#include <vector>

namespace dania {
struct VoiceStorageInfo {
    bool mounted = false, valid = false;
    std::string pack_id, error;
    uint32_t clips = 0;
    uint64_t total_bytes = 0, free_bytes = 0;
};
enum class LocalVoiceEvent { Accepted, Completed, Failed, Cancelled };
using LocalVoiceCallback = std::function<void(LocalVoiceEvent, const char*)>;
// Called once after LCD initialization. Reuses the existing SPI3 bus, never
// formats cards. All later status reads are cached (no FAT scan in BLE/UI).
void initialize_sd_voice(bool power_ready);
VoiceStorageInfo voice_storage_info();
std::vector<std::string> sd_headpet_ids();
bool has_sd_voice(std::string_view id);
// At most one pending/active request. A true return means queued, not audible.
// Accepted follows full CRC/Ogg validation and an exclusive audio reservation;
// Completed follows actual output drain. Callbacks run on the SD worker and
// must Schedule() application state/protocol work onto its owning task.
bool request_sd_voice(std::string_view id, LocalVoiceCallback callback = {});
void cancel_sd_voice();
}
