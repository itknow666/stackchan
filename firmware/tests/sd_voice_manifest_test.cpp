#include "../main/hal/board/sd_voice_manifest.h"
#include "../main/hal/board/agent_boot_policy.h"
#include <cassert>
#include <string>

int main() {
    using namespace dania;
    assert(select_agent_boot(false, false, -1) == AgentBoot::Agent);
    assert(select_agent_boot(false, true, 6) == AgentBoot::Agent);
    assert(select_agent_boot(true, false, 0) == AgentBoot::Maintenance);
    assert(safe_voice_id("01_hello") && safe_voice_id("cute-10"));
    assert(!safe_voice_id("../evil") && !safe_voice_id("a/b") && !safe_voice_id("a\\b"));
    assert(!safe_voice_id("") && !safe_voice_id(std::string(65, 'a')));
    assert(voice_crc32(0, reinterpret_cast<const unsigned char*>("123456789"), 9) == 0xcbf43926);
    const auto partial = voice_crc32(0, reinterpret_cast<const unsigned char*>("1234"), 4);
    assert(voice_crc32(partial, reinterpret_cast<const unsigned char*>("56789"), 5) == 0xcbf43926);
    const std::string clip = R"({"id":"01_hello","file":"01_hello.ogg","bytes":123,"crc32":1234,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"})";
    auto json = [&](const std::string& clips, const std::string& head = "[]") {
        return "{\"version\":1,\"pack_id\":\"dania-20261116\",\"clips\":[" + clips + "],\"headpet\":" + head + "}";
    };
    VoiceManifest manifest;
    std::string error;
    assert(parse_voice_manifest(json(clip, "[\"01_hello\"]"), manifest, error));
    assert(manifest.clips.size() == 1 && manifest.headpet.size() == 1);
    assert(!parse_voice_manifest(json(clip + "," + clip), manifest, error));
    assert(!parse_voice_manifest(json(clip, "[\"missing\"]"), manifest, error));
    assert(!parse_voice_manifest(json(clip, "[\"01_hello\",\"01_hello\"]"), manifest, error));
    for (const auto& pair : {std::pair{"01_hello.ogg", "../a.ogg"}, {"01_hello.ogg", "/a.ogg"}, {"\"bytes\":123", "\"bytes\":262145"}, {"\"bytes\":123", "\"bytes\":true"}, {"\"bytes\":123", "\"bytes\":0"}, {"\"crc32\":1234", "\"crc32\":-1"}, {"\"crc32\":1234", "\"crc32\":1.5"}}) {
        std::string bad = clip;
        bad.replace(bad.find(pair.first), std::string(pair.first).size(), pair.second);
        assert(!parse_voice_manifest(json(bad), manifest, error));
    }
    assert(!parse_voice_manifest("{}", manifest, error));
    assert(!parse_voice_manifest(json(clip) + "junk", manifest, error));
    assert(!parse_voice_manifest(std::string(32769, ' '), manifest, error));
    std::string too_many;
    for (int i=0;i<65;++i) {
        std::string item=clip;
        auto p=item.find("01_hello\"");
        item.replace(p, 8, "clip_" + std::to_string(i));
        if(i) too_many += ',';
        too_many += item;
    }
    assert(!parse_voice_manifest(json(too_many), manifest, error));
    assert(manifest.clips.empty()); // Failed parse cannot retain stale capabilities.
}
