#pragma once
#include <cstddef>

// Optional PRIVATE build input. Public builds return no files. The storage
// implementation owns all paths and writes; names are basenames only.
struct DaniaSdSeedFile {
    const char* name;
    const unsigned char* data;
    size_t size;
};
extern "C" const DaniaSdSeedFile* dania_sd_bootstrap_files(size_t* count);
