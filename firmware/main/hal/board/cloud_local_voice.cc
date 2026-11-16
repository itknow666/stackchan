#include "cloud_local_voice.h"
#include "sd_voice.h"
#include "sd_voice_manifest.h"
#include <application.h>
#include <esp_log.h>
#include <map>
#include <memory>

namespace dania {
namespace {
bool connected = false;
uint64_t generation = 0;
struct Request { std::string id; bool accepted = false; CloudLocalReply reply; };
std::shared_ptr<Request> active;
// Fixed budget per connection: never evict an ID and accidentally replay it.
std::map<std::string, std::string> results;
}
cJSON* local_audio_capabilities() {
    const auto info = voice_storage_info();
    const auto ids = sd_voice_ids();
    if(!info.valid || ids.empty() || ids.size()>MaxVoiceClips) return nullptr;
    auto* root=cJSON_CreateObject();
    cJSON_AddNumberToObject(root,"version",1);
    cJSON_AddStringToObject(root,"pack_id",info.pack_id.c_str());
    auto* clips=cJSON_AddArrayToObject(root,"clips");
    for(const auto& id:ids) cJSON_AddItemToArray(clips,cJSON_CreateString(id.c_str()));
    return root;
}
void cancel_cloud_local_voice(const std::string& request) {
    if(!request.empty() && (!active || active->id!=request)) {
        // Cancellation may arrive while an application task is deferred behind
        // STATE_CHANGED. Remember that exact ID without cancelling a newer one.
        if(safe_voice_id(request) && results.size()<64) results.emplace(request,"cancelled");
        return;
    }
    ++generation;
    if(!active) return;
    auto old=std::move(active);
    cancel_sd_voice();
    results[old->id]="cancelled";
    if(connected) old->reply(old->id,"cancelled");
}
uint64_t local_audio_generation() {return generation;}
void local_audio_connection(bool is_connected) {
    connected=false;
    cancel_cloud_local_voice();
    ++generation;
    results.clear();
    connected=is_connected;
}
void request_cloud_local_voice(std::string request,std::string pack,std::string clip,
                               int timeout_ms,CloudLocalReply reply) {
    if(!connected || !safe_voice_id(request)) return;
    if(active && active->id==request) {
        // A queued job may still start: never say unavailable for a duplicate
        // pending request, which would incorrectly authorize network fallback.
        if(active->accepted) reply(request,"accepted");
        return;
    }
    auto seen=results.find(request);
    if(seen!=results.end()) {reply(request,seen->second.c_str());return;}
    const auto info=voice_storage_info();
    auto& app=Application::GetInstance();
    if(active || results.size()>=64 || timeout_ms<1000 || timeout_ms>20000 ||
       !safe_voice_id(pack) || !safe_voice_id(clip) || !info.valid || info.pack_id!=pack ||
       !has_sd_voice(clip) || app.GetDeviceState()!=kDeviceStateSpeaking) {
        reply(request,"unavailable");return;
    }
    auto job=std::make_shared<Request>(Request{request,false,std::move(reply)});
    active=job;
    const auto epoch=++generation;
    results[request]="unavailable";
    const bool queued=request_sd_voice(clip,[job,epoch](LocalVoiceEvent event,const char*) {
        // The SD worker never touches application/protocol state directly.
        Application::GetInstance().Schedule([job,epoch,event]() {
            if(!connected || generation!=epoch || active!=job) return;
            const char* state="failed";
            if(event==LocalVoiceEvent::Accepted) {job->accepted=true;state="accepted";}
            else if(event==LocalVoiceEvent::Completed) state="completed";
            else if(event==LocalVoiceEvent::Cancelled) state="cancelled";
            else if(!job->accepted) state="unavailable";
            results[job->id]=state;
            job->reply(job->id,state);
            ESP_LOGI("DANIA_LOCAL","Cloud local recording state=%s",state);
            if(event!=LocalVoiceEvent::Accepted) active.reset();
        });
    });
    if(!queued) {active.reset();job->reply(request,"unavailable");}
}
}
