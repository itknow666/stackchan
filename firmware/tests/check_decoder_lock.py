"""Exercise the actual patched SetDecodeSampleRate with a blocking SDK stub.

Run after fetching/applying firmware dependencies:
  python3 tests/check_decoder_lock.py xiaozhi-esp32/main/audio/audio_service.cc
Only this method is compiled against host stubs, so no ESP SDK is required.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


def extract_method(source):
    start = source.index("void AudioService::SetDecodeSampleRate(")
    brace = source.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
        end += 1
    return source[start:end]


PREFIX = r'''
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <future>
#include <mutex>
#include <thread>
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
#define TAG "test"
constexpr int ESP_AUDIO_MONO = 1;
struct esp_opus_dec_cfg_t { int rate, duration; };
struct esp_ae_rate_cvt_cfg_t { int rate, output, channel; };
#define OPUS_DEC_CFG(rate, duration) esp_opus_dec_cfg_t{rate, duration}
#define RATE_CVT_CFG(rate, output, channel) esp_ae_rate_cvt_cfg_t{rate, output, channel}
struct Codec { int output_sample_rate() const { return 24000; } };
struct Board {
    static Board& GetInstance() { static Board board; return board; }
    Codec* GetAudioCodec() { return &codec; }
    Codec codec;
};
static std::mutex latch_mutex;
static std::condition_variable latch_cv;
static bool open_entered=false, release_open=false;
static int decoder_storage=0, resampler_storage=0;
int esp_opus_dec_close(void*) { return 0; }
int esp_opus_dec_open(const esp_opus_dec_cfg_t*, size_t, void** out) {
    std::unique_lock<std::mutex> lock(latch_mutex);
    open_entered=true;
    latch_cv.notify_all();
    latch_cv.wait(lock, [] { return release_open; });
    *out=&decoder_storage;
    return 0;
}
int esp_ae_rate_cvt_close(void*) { return 0; }
int esp_ae_rate_cvt_open(const esp_ae_rate_cvt_cfg_t*, void** out) {
    *out=&resampler_storage;
    return 0;
}
class AudioService {
public:
    std::mutex decoder_mutex_;
    void* opus_decoder_=&decoder_storage;
    void* output_resampler_=nullptr;
    int decoder_sample_rate_=24000, decoder_duration_ms_=60, decoder_frame_size_=1440;
    void SetDecodeSampleRate(int rate, int duration);
};
'''

SUFFIX = r'''
int main() {
    AudioService audio;
    std::thread configure([&] { audio.SetDecodeSampleRate(16000,20); });
    {
        std::unique_lock<std::mutex> lock(latch_mutex);
        assert(latch_cv.wait_for(lock,std::chrono::seconds(5),[] { return open_entered; }));
    }
    std::promise<void> attempted, completed;
    auto attempted_future=attempted.get_future(), completed_future=completed.get_future();
    std::thread reset([&] {
        attempted.set_value();
        // Same lock boundary used by ResetDecoder / EndLocalPlayback(cancel).
        std::lock_guard<std::mutex> lock(audio.decoder_mutex_);
        assert(audio.opus_decoder_==&decoder_storage);
        assert(audio.decoder_sample_rate_==16000);
        assert(audio.decoder_duration_ms_==20 && audio.decoder_frame_size_==320);
        assert(audio.output_resampler_==&resampler_storage);
        completed.set_value();
    });
    attempted_future.wait();
    // The create call is still inside its latch: reset must not enter while
    // the handle or its matching metadata is being created/published.
    assert(completed_future.wait_for(std::chrono::milliseconds(150))==std::future_status::timeout);
    {
        std::lock_guard<std::mutex> lock(latch_mutex);
        release_open=true;
        latch_cv.notify_all();
    }
    configure.join();
    assert(completed_future.wait_for(std::chrono::seconds(5))==std::future_status::ready);
    reset.join();
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio_service", type=Path)
    parser.add_argument("--compiler", default="c++")
    args = parser.parse_args()
    method = extract_method(args.audio_service.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="stackchan-decoder-lock-") as temporary:
        directory = Path(temporary)
        source = directory / "decoder_lock_test.cpp"
        binary = directory / "decoder_lock_test"
        source.write_text(PREFIX + method + SUFFIX, encoding="utf-8")
        subprocess.run([args.compiler, "-std=c++17", "-pthread", str(source), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=15)
    print("Actual decoder method: configuration/reset lock exclusion PASS")


if __name__ == "__main__":
    main()
