#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <sys/stat.h>

namespace dania {
int voice_path_stat(const char* path, struct stat* result);
bool voice_regular_file(const std::string& path, size_t expected);
bool voice_file_crc(const std::string& path, size_t bytes, uint32_t expected);
}
