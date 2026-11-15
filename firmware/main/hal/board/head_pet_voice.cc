#include "head_pet_voice.h"
#include "head_pet_voice_policy.h"
#include "hal_bridge.h"
#include <application.h>
#include <esp_random.h>
#include <esp_timer.h>
#include <esp_log.h>
#include <atomic>
#include <string_view>

#if DANIA_HEADPET_AUDIO
#define DANIA_CLIP(N) \
extern const char clip_##N##_start[] asm("_binary_dania_cute_" #N "_ogg_start"); \
extern const char clip_##N##_end[] asm("_binary_dania_cute_" #N "_ogg_end");
DANIA_CLIP(01) DANIA_CLIP(02) DANIA_CLIP(03) DANIA_CLIP(04) DANIA_CLIP(05)
DANIA_CLIP(06) DANIA_CLIP(07) DANIA_CLIP(08) DANIA_CLIP(09) DANIA_CLIP(10)
#undef DANIA_CLIP

namespace dania {
static std::atomic<bool> pending{false};
static HeadPetVoicePolicy policy;
static const std::string_view clips[] = {
#define DANIA_VIEW(N) {clip_##N##_start, static_cast<size_t>(clip_##N##_end - clip_##N##_start)}
    DANIA_VIEW(01), DANIA_VIEW(02), DANIA_VIEW(03), DANIA_VIEW(04), DANIA_VIEW(05),
    DANIA_VIEW(06), DANIA_VIEW(07), DANIA_VIEW(08), DANIA_VIEW(09), DANIA_VIEW(10)
#undef DANIA_VIEW
};

void request_head_pet_voice()
{
    if (!hal_bridge::is_xiaozhi_mode() || pending.exchange(true)) return;
    Application::GetInstance().Schedule([]() {
        pending.store(false);
        auto& app = Application::GetInstance();
        const uint32_t now = static_cast<uint32_t>(esp_timer_get_time() / 1000);
        const bool idle = app.GetDeviceState() == kDeviceStateIdle &&
                          app.GetAudioService().IsPlaybackIdle();
        if (!policy.ready(now, idle)) return;
        const unsigned index = policy.select(now, esp_random());
        ESP_LOGI("DANIA_HEAD_PET", "Playing local clip %u/10", index + 1);
        app.PlaySound(clips[index]);
    });
}
}
#else
// Private voice recordings are optional in the public source distribution.
namespace dania {
void request_head_pet_voice() {}
}
#endif
