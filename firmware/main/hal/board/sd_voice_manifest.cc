#include "sd_voice_manifest.h"
#include <ArduinoJson.hpp>
#include <algorithm>
#include <cstring>

namespace dania {
bool safe_voice_id(std::string_view value) {
    if (value.empty() || value.size() > 64) return false;
    for (unsigned char c : value)
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-')) return false;
    return true;
}
bool safe_voice_file(std::string_view value) {
    return value.size() > 4 && value.substr(value.size()-4) == ".ogg" &&
           safe_voice_id(value.substr(0, value.size()-4));
}
uint32_t voice_crc32(uint32_t previous, const unsigned char* data, size_t size) {
    uint32_t crc = ~previous;
    for (size_t i=0;i<size;++i) {
        crc ^= data[i];
        for (int bit=0;bit<8;++bit) crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}
namespace {
// Track the parser's consumed bytes so valid JSON followed by arbitrary input
// cannot be accepted as the currently advertised manifest.
struct Reader {
    std::string_view input;
    size_t at = 0;
    int read() { return at < input.size() ? static_cast<unsigned char>(input[at++]) : -1; }
    size_t readBytes(char* out, size_t n) {
        n = std::min(n, input.size()-at);
        std::memcpy(out, input.data()+at, n);
        at += n;
        return n;
    }
};
bool has_nul(ArduinoJson::JsonString value) {
    return value.isNull() || std::strlen(value.c_str()) != value.size();
}
}
bool parse_voice_manifest(std::string_view json, VoiceManifest& out, std::string& error) {
    using namespace ArduinoJson;
    out = {};
    auto fail = [&](const char* why) { error = why; return false; };
    if (json.empty() || json.size() > MaxVoiceManifestBytes) return fail("manifest_size");
    JsonDocument doc;
    Reader reader{json};
    if (deserializeJson(doc, reader, DeserializationOption::NestingLimit(5))) return fail("manifest_json");
    for (;reader.at<json.size();++reader.at)
        if (json[reader.at]!=' ' && json[reader.at]!='\r' && json[reader.at]!='\n' && json[reader.at]!='\t') return fail("manifest_trailing");
    if (!doc.is<JsonObject>() || !doc["version"].is<int>() || doc["version"].as<int>()!=1 ||
        !doc["pack_id"].is<JsonString>() || has_nul(doc["pack_id"].as<JsonString>())) return fail("manifest_version");
    VoiceManifest next;
    next.pack_id = doc["pack_id"].as<const char*>();
    if (!safe_voice_id(next.pack_id)) return fail("manifest_pack");
    if (!doc["clips"].is<JsonArray>() || doc["clips"].size()==0 || doc["clips"].size()>MaxVoiceClips) return fail("manifest_clips");
    for (JsonVariant value : doc["clips"].as<JsonArray>()) {
        if (!value.is<JsonObject>() || !value["id"].is<JsonString>() || !value["file"].is<JsonString>() ||
            !value["sha256"].is<JsonString>() || has_nul(value["id"].as<JsonString>()) ||
            has_nul(value["file"].as<JsonString>()) || has_nul(value["sha256"].as<JsonString>()) ||
            !value["bytes"].is<uint32_t>() || !value["crc32"].is<uint32_t>()) return fail("manifest_clip_fields");
        VoiceClip clip{value["id"].as<const char*>(), value["file"].as<const char*>(), value["sha256"].as<const char*>(),
                       value["bytes"].as<uint32_t>(), value["crc32"].as<uint32_t>()};
        if (!safe_voice_id(clip.id) || !safe_voice_file(clip.file) || clip.bytes==0 || clip.bytes>MaxVoiceClipBytes ||
            clip.sha256.size()!=64 || !std::all_of(clip.sha256.begin(), clip.sha256.end(), [](char c) {
                return (c>='0' && c<='9') || (c>='a' && c<='f') || (c>='A' && c<='F');
            })) return fail("manifest_clip_bounds");
        for (const auto& old : next.clips)
            if (old.id==clip.id || old.file==clip.file) return fail("manifest_duplicate");
        next.clips.push_back(std::move(clip));
    }
    if (!doc["headpet"].is<JsonArray>() || doc["headpet"].size()>10) return fail("manifest_headpet");
    for (JsonVariant value : doc["headpet"].as<JsonArray>()) {
        if (!value.is<JsonString>() || has_nul(value.as<JsonString>())) return fail("manifest_headpet_id");
        const std::string id = value.as<const char*>();
        if (std::find(next.headpet.begin(), next.headpet.end(), id)!=next.headpet.end() ||
            std::none_of(next.clips.begin(), next.clips.end(), [&](const auto& c) { return c.id==id; })) return fail("manifest_headpet_missing");
        next.headpet.push_back(id);
    }
    out = std::move(next);
    error.clear();
    return true;
}
}
