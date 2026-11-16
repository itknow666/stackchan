"""Compile the actual cloud-local-voice dispatcher with scheduled application/SD fakes."""
import argparse
from pathlib import Path
import subprocess
import tempfile

parser=argparse.ArgumentParser()
parser.add_argument('--compiler',default='c++')
args=parser.parse_args()
board=Path(__file__).resolve().parents[1]/'main/hal/board'
source=(board/'cloud_local_voice.cc').read_text()
source='\n'.join(line for line in source.splitlines() if not line.startswith('#include'))
prelude=r'''
#include <cassert>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>
#include <deque>
#include "sd_voice.h"
#include "sd_voice_manifest.h"
struct cJSON{};
cJSON* cJSON_CreateObject(){return nullptr;}
void cJSON_AddNumberToObject(cJSON*,const char*,int){}
void cJSON_AddStringToObject(cJSON*,const char*,const char*){}
cJSON* cJSON_AddArrayToObject(cJSON*,const char*){return nullptr;}
cJSON* cJSON_CreateString(const char*){return nullptr;}
void cJSON_AddItemToArray(cJSON*,cJSON*){}
#define ESP_LOGI(...) ((void)0)
enum {kDeviceStateIdle,kDeviceStateSpeaking};
struct Application {
 int state=kDeviceStateSpeaking;
 std::deque<std::function<void()>> pending;
 static Application& GetInstance(){static Application app;return app;}
 void Schedule(std::function<void()> fn){pending.push_back(std::move(fn));}
 int GetDeviceState(){return state;}
 void flush(){while(!pending.empty()){auto fn=std::move(pending.front());pending.pop_front();fn();}}
};
namespace dania {
using CloudLocalReply=std::function<void(const std::string&,const char*)>;
void cancel_cloud_local_voice(const std::string& request = {});
VoiceStorageInfo storage{true,true,"pack","",1,100,50};
LocalVoiceCallback worker_callback;
int requests=0,cancels=0;
bool queue_allowed=true;
VoiceStorageInfo voice_storage_info(){return storage;}
std::vector<std::string> sd_voice_ids(){return {"clip"};}
bool has_sd_voice(std::string_view id){return id=="clip";}
bool safe_voice_id(std::string_view id){return !id.empty() && id.size()<=64 && id.find('/')==id.npos;}
bool request_sd_voice(std::string_view,LocalVoiceCallback cb){requests++;worker_callback=cb;return queue_allowed;}
void cancel_sd_voice(){cancels++;}
}
'''
harness=r'''
int main(){
 using namespace dania;
 std::vector<std::string> replies;
 auto reply=[&](const std::string& id,const char* state){replies.push_back(id+":"+state);};
 auto& app=Application::GetInstance();
 local_audio_connection(true);
 request_cloud_local_voice("one","pack","clip",20000,reply);
 assert(requests==1 && replies.empty());
 request_cloud_local_voice("one","pack","clip",20000,reply);
 assert(requests==1 && replies.empty()); // duplicate pending must NOT permit fallback
 worker_callback(LocalVoiceEvent::Accepted,"accepted");
 assert(replies.empty());app.flush();
 assert(replies.back()=="one:accepted");
 worker_callback(LocalVoiceEvent::Completed,"completed");app.flush();
 assert(replies.back()=="one:completed");
 request_cloud_local_voice("one","pack","clip",20000,reply);
 assert(requests==1); // terminal duplicate cannot replay

 request_cloud_local_voice("two","pack","clip",20000,reply);
 worker_callback(LocalVoiceEvent::Failed,"CRC");app.flush();
 assert(replies.back()=="two:unavailable");
 request_cloud_local_voice("three","pack","clip",20000,reply);
 worker_callback(LocalVoiceEvent::Accepted,"accepted");app.flush();
 worker_callback(LocalVoiceEvent::Failed,"output");app.flush();
 assert(replies.back()=="three:failed"); // accepted failure cannot fall back

 request_cloud_local_voice("old","pack","clip",20000,reply);
 auto old_callback=worker_callback;
 local_audio_connection(false);assert(cancels==1);
 local_audio_connection(true);
 request_cloud_local_voice("new","pack","clip",20000,reply);
 const auto count=replies.size();
 old_callback(LocalVoiceEvent::Accepted,"old");old_callback(LocalVoiceEvent::Completed,"old");app.flush();
 assert(replies.size()==count); // old connection callbacks cannot touch new request
 cancel_cloud_local_voice("wrong");assert(cancels==1);
 cancel_cloud_local_voice("new");assert(cancels==2 && replies.back()=="new:cancelled");
 worker_callback(LocalVoiceEvent::Completed,"late");app.flush();
 assert(replies.back()=="new:cancelled");

 request_cloud_local_voice("bad","other-pack","clip",20000,reply);
 assert(replies.back()=="bad:unavailable");
 app.state=kDeviceStateIdle;
 request_cloud_local_voice("idle","pack","clip",20000,reply);
 assert(replies.back()=="idle:unavailable");
 app.state=kDeviceStateSpeaking;queue_allowed=false;
 request_cloud_local_voice("busy","pack","clip",20000,reply);
 assert(replies.back()=="busy:unavailable");
 queue_allowed=true;
 const auto requested=requests;
 cancel_cloud_local_voice("deferred");
 request_cloud_local_voice("deferred","pack","clip",20000,reply);
 assert(requests==requested && replies.back()=="deferred:cancelled");
 const auto epoch=local_audio_generation();
 cancel_cloud_local_voice();
 assert(local_audio_generation()!=epoch); // invalidates tasks queued before a new tts:start
}
'''
with tempfile.TemporaryDirectory(prefix='stackchan-cloud-local-') as folder:
    root=Path(folder);test=root/'test.cpp';test.write_text(prelude+source+harness)
    binary=root/'test'
    subprocess.run([args.compiler,'-std=c++17','-Wall','-Wextra','-Werror','-I',str(board),str(test),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
    # Exercise the actual SD enqueue function as well: a slow initial FAT scan
    # must reject before queueing and before consuming the playback deadline.
    sd_source=(board/'sd_voice.cc').read_text()
    start=sd_source.index('bool request_sd_voice(')
    end=sd_source.index('\nvoid cancel_sd_voice()',start)
    enqueue=sd_source[start:end]
    sd_test=r'''
#include <algorithm>
#include <atomic>
#include <cassert>
#include <memory>
#include <mutex>
#include "sd_voice.h"
#include "sd_voice_manifest.h"
int notifications=0;
void xTaskNotifyGive(void*){++notifications;}
int64_t esp_timer_get_time(){return 1000000;}
struct Application {
 struct Audio{uint32_t GetPlaybackGeneration(){return 7;}} audio;
 static Application& GetInstance(){static Application app;return app;}
 Audio& GetAudioService(){return audio;}
};
namespace dania {
std::mutex state_mutex;
VoiceStorageInfo info{true,true,"pack","",1,0,0};
VoiceManifest manifest{"pack",{{"clip","clip.ogg","",1,0}}, {}};
void* worker=reinterpret_cast<void*>(1);
std::atomic<bool> busy{false},worker_ready{false};
std::atomic<uint32_t> request_generation{3};
struct Job {VoiceClip clip;LocalVoiceCallback callback;uint32_t generation,audio_generation;int64_t deadline;};
std::unique_ptr<Job> next_job;
''' + enqueue + r'''
}
int main(){
 using namespace dania;
 assert(!request_sd_voice("clip") && !next_job && !busy && notifications==0);
 worker_ready=true;
 assert(!request_sd_voice("missing") && !next_job && notifications==0);
 assert(request_sd_voice("clip") && next_job && busy && notifications==1);
 assert(next_job->generation==3 && next_job->audio_generation==7 && next_job->deadline==21000000);
 assert(!request_sd_voice("clip") && notifications==1);
}
'''
    test.write_text(sd_test)
    subprocess.run([args.compiler,'-std=c++17','-Wall','-Wextra','-Werror','-I',str(board),str(test),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
print('Actual cloud local voice lifecycle and scheduled callback checks passed')
