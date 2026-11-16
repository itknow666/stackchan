#include "sd_voice.h"
#include "sd_voice_manifest.h"
#include "sd_voice_file.h"
#include "sd_voice_seed.h"
#include <application.h>
#include <ogg_demuxer.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <esp_vfs_fat.h>
#include <driver/sdspi_host.h>
#include <sdmmc_cmd.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_heap_caps.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>

extern "C" __attribute__((weak)) const DaniaSdSeedFile* dania_sd_bootstrap_files(size_t* count) {
    *count = 0;
    return nullptr;
}
namespace dania {
namespace {
constexpr const char* Tag = "DANIA_SD";
constexpr const char* Mount = "/sdcard";
constexpr const char* Pack = "/sdcard/dania/voice-pack";
constexpr const char* ManifestPath = "/sdcard/dania/voice-pack/manifest.json";
std::mutex state_mutex;
VoiceStorageInfo info;
VoiceManifest manifest;
sdmmc_card_t* card = nullptr;
TaskHandle_t worker = nullptr;
std::atomic<bool> busy{false};
std::atomic<uint32_t> request_generation{1};
struct Job { VoiceClip clip; LocalVoiceCallback callback; uint32_t generation, audio_generation; int64_t deadline; };
std::unique_ptr<Job> next_job;
using File = std::unique_ptr<FILE, decltype(&std::fclose)>;
std::string path_for(const std::string& name) { return std::string(Pack) + "/" + name; }

bool directory(const char* path) {
    struct stat st{};
    if (voice_path_stat(path, &st)==0) return S_ISDIR(st.st_mode);
    return mkdir(path, 0755)==0;
}
bool verify_ogg(FILE* file, const VoiceClip& clip) {
    std::rewind(file);
    auto demux = std::make_unique<OggDemuxer>();
    int duration=0;
    bool valid=true;
    demux->OnPacket([&](const uint8_t*, int rate, int ms, size_t bytes) {
        duration+=ms;
        if ((rate!=8000 && rate!=12000 && rate!=16000 && rate!=24000 && rate!=48000) ||
            ms<=0 || duration>MaxVoiceDurationMs || bytes==0) valid=false;
    });
    std::array<unsigned char, 1024> buffer{};
    size_t total=0, got=0;
    uint32_t crc=0;
    while ((got=std::fread(buffer.data(),1,buffer.size(),file))!=0) {
        total+=got;
        if(total>clip.bytes) return false;
        crc=voice_crc32(crc,buffer.data(),got);
        if(demux->Process(buffer.data(),got)!=got || demux->HasError() || !valid) return false;
    }
    return !std::ferror(file) && total==clip.bytes && crc==clip.crc32 &&
           valid && duration>0 && demux->Finish();
}

void bootstrap() {
    // Never replace a user's existing manifest, including a corrupt one.
    struct stat st{};
    if(voice_path_stat(ManifestPath,&st)==0) return;
    size_t count=0;
    const auto* files=dania_sd_bootstrap_files(&count);
    if(!files || count<2 || count>MaxVoiceClips+1) return;
    const DaniaSdSeedFile* manifest_file=nullptr;
    size_t total=0;
    for(size_t i=0;i<count;++i) {
        const auto& f=files[i];
        if(!f.name || !f.data || f.size==0) return;
        if(std::strcmp(f.name,"manifest.json")==0) {
            if(manifest_file || f.size>MaxVoiceManifestBytes) return;
            manifest_file=&f;
        } else if(!safe_voice_file(f.name) || f.size>MaxVoiceClipBytes) return;
        for(size_t j=0;j<i;++j) if(std::strcmp(f.name,files[j].name)==0) return;
        total+=f.size;
        if(total>2*1024*1024) return;
    }
    VoiceManifest seed;
    std::string error;
    if(!manifest_file || !parse_voice_manifest(
        {reinterpret_cast<const char*>(manifest_file->data),manifest_file->size},seed,error) ||
        seed.clips.size()+1!=count) return;
    // Every provided byte array must agree with its declared CRC/size before
    // creating anything. No unknown resource can be overwritten on a retry.
    for(const auto& clip:seed.clips) {
        const auto* found=std::find_if(files,files+count,[&](const auto& f){return clip.file==f.name;});
        if(found==files+count || found->size!=clip.bytes ||
           voice_crc32(0,found->data,found->size)!=clip.crc32) return;
    }
    if(!directory("/sdcard/dania") || !directory(Pack)) return;
    auto install=[&](const DaniaSdSeedFile& f) {
        const auto path=path_for(f.name);
        const uint32_t crc=voice_crc32(0,f.data,f.size);
        if(voice_path_stat(path.c_str(),&st)==0) return voice_file_crc(path,f.size,crc);
        const auto temporary=path+".seed-tmp";
        // A pre-existing temporary file may belong to an interrupted write;
        // leave it for manual recovery rather than silently destroying it.
        if(voice_path_stat(temporary.c_str(),&st)==0) return false;
        const int descriptor=open(temporary.c_str(),O_CREAT|O_EXCL|O_WRONLY,0644);
        if(descriptor<0) return false;
        File output(fdopen(descriptor,"wb"),&std::fclose);
        if(!output) {close(descriptor);return false;}
        bool ok=std::fwrite(f.data,1,f.size,output.get())==f.size && std::fflush(output.get())==0;
        output.reset();
        if(!ok || !voice_file_crc(temporary,f.size,crc)) return false;
        return std::rename(temporary.c_str(),path.c_str())==0;
    };
    for(size_t i=0;i<count;++i) if(&files[i]!=manifest_file && !install(files[i])) return;
    if(install(*manifest_file)) ESP_LOGI(Tag,"Private voice pack initialized on SD (%u clips)",static_cast<unsigned>(seed.clips.size()));
}

void load_manifest() {
    struct stat st{};
    if(voice_path_stat(ManifestPath,&st)!=0 || !S_ISREG(st.st_mode) || st.st_size<=0 || st.st_size>MaxVoiceManifestBytes) {
        info.error="manifest_missing_or_size"; return;
    }
    File file(std::fopen(ManifestPath,"rb"),&std::fclose);
    std::string text(static_cast<size_t>(st.st_size),'\0');
    if(!file || std::fread(text.data(),1,text.size(),file.get())!=text.size() || std::ferror(file.get())) {
        info.error="manifest_read"; return;
    }
    VoiceManifest loaded;
    if(!parse_voice_manifest(text,loaded,info.error)) return;
    // Validate all declared assets before advertising a compatible pack.
    for(const auto& clip:loaded.clips) {
        if(!voice_file_crc(path_for(clip.file),clip.bytes,clip.crc32)) {
            info.error="clip_crc_or_size"; return;
        }
    }
    manifest=std::move(loaded);
    info.valid=true;
    info.pack_id=manifest.pack_id;
    info.clips=manifest.clips.size();
    info.error.clear();
}

void run_job(Job& job) {
    auto emit=[&](LocalVoiceEvent event,const char* reason) { if(job.callback) job.callback(event,reason); };
    auto current=[&]() {return request_generation.load()==job.generation;};
    const auto path=path_for(job.clip.file);
    if(!current()) {emit(LocalVoiceEvent::Cancelled,"cancelled");return;}
    if(esp_timer_get_time()>job.deadline) {emit(LocalVoiceEvent::Failed,"expired");return;}
    File file(voice_regular_file(path,job.clip.bytes)?std::fopen(path.c_str(),"rb"):nullptr,&std::fclose);
    if(!file || !verify_ogg(file.get(),job.clip)) {
        {
            std::lock_guard<std::mutex> lock(state_mutex);
            info.valid=false;
            info.error="clip_invalid";
        }
        emit(LocalVoiceEvent::Failed,"clip_invalid");return;
    }
    if(!current()) {emit(LocalVoiceEvent::Cancelled,"cancelled");return;}
    if(esp_timer_get_time()>job.deadline) {emit(LocalVoiceEvent::Failed,"expired");return;}
    auto& audio=Application::GetInstance().GetAudioService();
    const uint32_t token=audio.BeginLocalPlayback(job.audio_generation);
    if(!token) {emit(LocalVoiceEvent::Failed,"audio_busy");return;}
    emit(LocalVoiceEvent::Accepted,"accepted");
    std::rewind(file.get());
    auto demux=std::make_unique<OggDemuxer>();
    bool ok=true;
    const int64_t deadline=job.deadline;
    demux->OnPacket([&](const uint8_t* bytes,int rate,int ms,size_t size) {
        if(!ok || !current() || esp_timer_get_time()>deadline) {ok=false;return;}
        auto packet=std::make_unique<AudioStreamPacket>();
        packet->sample_rate=rate;
        packet->frame_duration=ms;
        packet->payload.assign(bytes,bytes+size);
        ok=audio.PushLocalPacket(std::move(packet),token);
    });
    std::array<unsigned char,1024> buffer{};
    size_t got=0;
    while(ok && current() && (got=std::fread(buffer.data(),1,buffer.size(),file.get()))!=0) {
        if(demux->Process(buffer.data(),got)!=got || demux->HasError()) ok=false;
    }
    ok=ok && !std::ferror(file.get()) && demux->Finish();
    while(ok && current() && audio.IsLocalPlaybackCurrent(token) &&
          !audio.IsLocalPlaybackDrained(token) && esp_timer_get_time()<deadline) vTaskDelay(pdMS_TO_TICKS(10));
    const bool cancelled=!current() || !audio.IsLocalPlaybackCurrent(token);
    const bool completed=ok && !cancelled && audio.IsLocalPlaybackDrained(token);
    audio.EndLocalPlayback(token,!completed);
    emit(cancelled?LocalVoiceEvent::Cancelled:(completed?LocalVoiceEvent::Completed:LocalVoiceEvent::Failed),
         cancelled?"cancelled":(completed?"completed":"playback_failed"));
}
void worker_main(void*) {
    // Counting free FAT clusters on a large card can take seconds. Do it once
    // on this worker, never in startup/UI/BLE/audio producer callbacks.
    uint64_t total=0,free=0;
    if(esp_vfs_fat_info(Mount,&total,&free)==ESP_OK) {
        std::lock_guard<std::mutex> lock(state_mutex);
        info.total_bytes=total;
        info.free_bytes=free;
        ESP_LOGI(Tag,"SD capacity total_MB=%llu free_MB=%llu",total/(1024*1024),free/(1024*1024));
    }
    for(;;) {
        ulTaskNotifyTake(pdTRUE,portMAX_DELAY);
        std::unique_ptr<Job> job;
        {std::lock_guard<std::mutex> lock(state_mutex);job=std::move(next_job);}
        if(job) run_job(*job);
        busy.store(false);
    }
}
}

void initialize_sd_voice(bool power_ready) {
    std::lock_guard<std::mutex> lock(state_mutex);
    if(!power_ready) {info.error="sd_power";return;}
    sdmmc_host_t host=SDSPI_HOST_DEFAULT();
    host.slot=SPI3_HOST;
    host.max_freq_khz=10000;
    sdspi_device_config_t device=SDSPI_DEVICE_CONFIG_DEFAULT();
    device.host_id=SPI3_HOST;
    device.gpio_cs=GPIO_NUM_4;
    esp_vfs_fat_sdmmc_mount_config_t mount{};
    mount.format_if_mount_failed=false;
    mount.max_files=4;
    mount.allocation_unit_size=16*1024;
    const auto result=esp_vfs_fat_sdspi_mount(Mount,&host,&device,&mount,&card);
    if(result!=ESP_OK) {info.error="mount_failed";ESP_LOGW(Tag,"SD unavailable (%s); embedded/network audio retained",esp_err_to_name(result));return;}
    info.mounted=true;
    bootstrap();
    load_manifest();
    // A single low-priority, PSRAM-stack producer feeds the bounded decode queue.
    if(xTaskCreateWithCaps(worker_main,"sd_voice",8192,nullptr,2,&worker,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)!=pdPASS) {
        info.valid=false;info.error="worker_memory";
    }
    ESP_LOGI(Tag,"Mounted SD; valid=%d clips=%u",info.valid,static_cast<unsigned>(info.clips));
}
VoiceStorageInfo voice_storage_info() {std::lock_guard<std::mutex> lock(state_mutex);return info;}
std::vector<std::string> sd_headpet_ids() {std::lock_guard<std::mutex> lock(state_mutex);return info.valid?manifest.headpet:std::vector<std::string>{};}
bool has_sd_voice(std::string_view id) {
    std::lock_guard<std::mutex> lock(state_mutex);
    return info.valid && std::any_of(manifest.clips.begin(),manifest.clips.end(),[&](const auto& clip){return clip.id==id;});
}
bool request_sd_voice(std::string_view id,LocalVoiceCallback callback) {
    std::lock_guard<std::mutex> lock(state_mutex);
    if(!info.valid || !worker || busy.load()) return false;
    auto found=std::find_if(manifest.clips.begin(),manifest.clips.end(),[&](const auto& c){return c.id==id;});
    if(found==manifest.clips.end()) return false;
    busy.store(true);
    next_job=std::make_unique<Job>(Job{*found,std::move(callback),request_generation.load(),
        Application::GetInstance().GetAudioService().GetPlaybackGeneration(),esp_timer_get_time()+20000000});
    xTaskNotifyGive(worker);
    return true;
}
void cancel_sd_voice() {request_generation.fetch_add(1);}
}
