#include "sd_voice_file.h"
#include "sd_voice_manifest.h"
#include <array>
#include <cstdio>
#include <memory>
#include <sys/stat.h>

namespace dania {
int voice_path_stat(const char* path, struct stat* result) {
#ifdef ESP_PLATFORM
    // ESP-IDF's FAT VFS has no symlinks and exposes stat, not lstat.
    return stat(path,result);
#else
    return lstat(path,result);
#endif
}
bool voice_regular_file(const std::string& path, size_t expected) {
    struct stat st{};
    return voice_path_stat(path.c_str(), &st)==0 && S_ISREG(st.st_mode) &&
           st.st_size>=0 && static_cast<uint64_t>(st.st_size)==expected;
}
bool voice_file_crc(const std::string& path, size_t bytes, uint32_t expected) {
    if(bytes==0 || bytes>MaxVoiceClipBytes || !voice_regular_file(path,bytes)) return false;
    std::unique_ptr<FILE,decltype(&std::fclose)> file(std::fopen(path.c_str(),"rb"),&std::fclose);
    if(!file) return false;
    std::array<unsigned char,1024> buffer{};
    uint32_t crc=0;
    size_t total=0,got=0;
    while((got=std::fread(buffer.data(),1,buffer.size(),file.get()))!=0) {
        total+=got;
        if(total>bytes) return false;
        crc=voice_crc32(crc,buffer.data(),got);
    }
    return !std::ferror(file.get()) && total==bytes && crc==expected;
}
}
