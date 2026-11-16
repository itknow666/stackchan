#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace dania {
constexpr size_t MaxVoiceManifestBytes = 32768;
constexpr size_t MaxVoiceClips = 64;
constexpr size_t MaxVoiceClipBytes = 256 * 1024;
constexpr int MaxVoiceDurationMs = 15000;
struct VoiceClip {
    std::string id, file, sha256;
    size_t bytes = 0;
    uint32_t crc32 = 0;
};
struct VoiceManifest {
    std::string pack_id;
    std::vector<VoiceClip> clips;
    std::vector<std::string> headpet;
};
bool safe_voice_id(std::string_view value);
bool safe_voice_file(std::string_view value);
uint32_t voice_crc32(uint32_t previous, const unsigned char* data, size_t size);
bool parse_voice_manifest(std::string_view json, VoiceManifest& out, std::string& error);
}
