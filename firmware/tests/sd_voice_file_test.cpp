#include "../main/hal/board/sd_voice_file.h"
#include "../main/hal/board/sd_voice_manifest.h"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <string>
#include <unistd.h>
int main() {
    char pattern[]="/tmp/stackchan-voice-test-XXXXXX";
    const char* created=mkdtemp(pattern);
    assert(created);
    const std::filesystem::path dir(created);
    const auto path=(dir/"clip.ogg").string();
    // Cross several 1 KiB read boundaries and exercise incremental CRC.
    const std::string content(7231,'x');
    std::ofstream(path,std::ios::binary).write(content.data(),content.size());
    const auto crc=dania::voice_crc32(0,reinterpret_cast<const unsigned char*>(content.data()),content.size());
    assert(dania::voice_file_crc(path,content.size(),crc));
    assert(!dania::voice_file_crc(path,content.size(),crc^1));
    assert(!dania::voice_file_crc(path,content.size()+1,crc));
    assert(!dania::voice_file_crc(path,content.size()-1,crc));
    assert(!dania::voice_file_crc(path,0,crc));
    assert(!dania::voice_file_crc(path,dania::MaxVoiceClipBytes+1,crc));
    assert(!dania::voice_file_crc((dir/"missing").string(),content.size(),crc));
    std::filesystem::create_symlink(path,dir/"link.ogg");
    assert(!dania::voice_file_crc((dir/"link.ogg").string(),content.size(),crc));
    assert(!dania::voice_regular_file(dir.string(),content.size()));
    std::ofstream(path,std::ios::binary|std::ios::trunc).write("bad",3);
    assert(!dania::voice_file_crc(path,content.size(),crc));
    std::filesystem::remove_all(dir);
}
