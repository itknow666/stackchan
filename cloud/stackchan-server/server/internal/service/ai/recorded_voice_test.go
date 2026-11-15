package ai

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func testVoicePack(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	pack := recordedVoicePack{Enabled: true, SampleRate: 24000, Channels: 1, Encoding: "pcm_s16le", Aliases: []string{"达妮娅"}, Rules: []recordedVoiceRule{{File: "welcome.pcm", Reply: "欢迎回来", Triggers: []string{"我回来了"}}, {File: "welcome.pcm", Reply: "庆祝一下", Triggers: []string{"我成功了"}}}}
	data, _ := json.Marshal(pack)
	if err := os.WriteFile(filepath.Join(dir, "manifest.json"), data, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "welcome.pcm"), []byte{1, 0, 255, 255}, 0600); err != nil {
		t.Fatal(err)
	}
	return dir
}

func TestRecordedVoiceMatchesWholeUtterance(t *testing.T) {
	dir := testVoicePack(t)
	for _, text := range []string{"我回来了", "我回来了！", " 达妮娅，我回来了。 "} {
		clip, ok, err := recordedVoiceForText(dir, text)
		if err != nil || !ok || clip.Reply != "欢迎回来" || !reflect.DeepEqual(clip.PCM, []int16{1, -1}) {
			t.Fatalf("match %q = %#v, %v, %v", text, clip, ok, err)
		}
	}
	for _, text := range []string{"我没有成功", "我没成功", "如果我成功了呢", "你为什么说我回来了", "我成功了吗", "我还没回来了", "我回来了是什么意思", "不要说我回来了"} {
		_, ok, err := recordedVoiceForText(dir, text)
		if err != nil || ok {
			t.Fatalf("false trigger for %q: %v, %v", text, ok, err)
		}
	}
}

func TestRecordedVoiceRejectsBadAssets(t *testing.T) {
	dir := testVoicePack(t)
	for _, data := range [][]byte{{}, {1}} {
		if err := os.WriteFile(filepath.Join(dir, "welcome.pcm"), data, 0600); err != nil {
			t.Fatal(err)
		}
		if _, ok, err := recordedVoiceForText(dir, "我回来了"); ok || err == nil {
			t.Fatal("accepted incomplete audio")
		}
	}
	p := filepath.Join(dir, "manifest.json")
	data, _ := os.ReadFile(p)
	var pack recordedVoicePack
	_ = json.Unmarshal(data, &pack)
	pack.Rules[0].File = "../outside.pcm"
	data, _ = json.Marshal(pack)
	_ = os.WriteFile(p, data, 0600)
	if _, ok, err := recordedVoiceForText(dir, "我回来了"); ok || err == nil {
		t.Fatal("accepted escaping path")
	}
}

func TestCompatibleSessionPlaysRecordingWithoutChatOrTTS(t *testing.T) {
	t.Setenv("STACKCHAN_VOICE_PACK_DIR", testVoicePack(t))
	var events []string
	cb := RealtimeCallbacks{OnSTT: func(string) { events = append(events, "stt") }, OnText: func(s string) {
		if s != "欢迎回来" {
			t.Errorf("reply=%s", s)
		}
		events = append(events, "text")
	}, OnStart: func() { events = append(events, "start") }, OnAudio: func(p []int16) {
		if !reflect.DeepEqual(p, []int16{1, -1}) {
			t.Errorf("audio=%v", p)
		}
		events = append(events, "audio")
	}, OnStop: func() { events = append(events, "stop") }}
	s := compatibleSessionForTest(t, cb, func(path string) (*http.Response, error) {
		if strings.HasSuffix(path, "/audio/transcriptions") {
			return jsonResponse(`{"text":"我回来了"}`), nil
		}
		t.Errorf("recorded trigger unexpectedly called %s", path)
		return nil, fmt.Errorf("unexpected cloud call")
	})
	s.completeTurn(context.Background(), make([]int16, deviceFrameSamples))
	if strings.Join(events, ",") != "stt,text,start,audio,stop" {
		t.Fatalf("events=%v", events)
	}
	if len(s.history) != 2 || s.history[1].Content != "欢迎回来" {
		t.Fatalf("recorded reply lost from conversation history")
	}
}

func TestRecordedVoiceFailureFallsBackToNormalConversation(t *testing.T) {
	dir := testVoicePack(t)
	t.Setenv("STACKCHAN_VOICE_PACK_DIR", dir)
	_ = os.Remove(filepath.Join(dir, "welcome.pcm"))
	var paths []string
	s := compatibleSessionForTest(t, RealtimeCallbacks{}, func(path string) (*http.Response, error) {
		paths = append(paths, path)
		switch {
		case strings.HasSuffix(path, "/audio/transcriptions"):
			return jsonResponse(`{"text":"我回来了"}`), nil
		case strings.HasSuffix(path, "/chat/completions"):
			return jsonResponse(`{"choices":[{"message":{"content":"你好"}}]}`), nil
		case strings.HasSuffix(path, "/audio/speech"):
			return pcmResponse(480), nil
		}
		return nil, fmt.Errorf("unexpected API path")
	})
	s.completeTurn(context.Background(), make([]int16, deviceFrameSamples))
	if len(paths) != 3 {
		t.Fatalf("missing audio blocked normal conversation: %v", paths)
	}
}

func TestConfiguredRecordedVoicePack(t *testing.T) {
	dir := os.Getenv("STACKCHAN_TEST_VOICE_PACK_DIR")
	if dir == "" {
		t.Skip("no deployment voice pack supplied")
	}
	data, err := os.ReadFile(filepath.Join(dir, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var pack recordedVoicePack
	if err = json.Unmarshal(data, &pack); err != nil {
		t.Fatal(err)
	}
	if len(pack.Rules) != 21 {
		t.Fatalf("want 21 clips, got %d", len(pack.Rules))
	}
	for _, rule := range pack.Rules {
		for _, trigger := range rule.Triggers {
			clip, ok, err := recordedVoiceForText(dir, trigger)
			if err != nil || !ok || clip.Reply != rule.Reply || len(clip.PCM) == 0 {
				t.Fatalf("invalid deployed rule %s: %v", rule.File, err)
			}
		}
	}
	for _, text := range []string{"我没有成功", "我没成功", "我不是笨蛋", "我不累", "我还没回家", "别夸我", "不要打哈欠", "我不想睡觉", "你为什么说晚安", "帮我解释你好这个词", "如果我成功了呢"} {
		if _, ok, err := recordedVoiceForText(dir, text); ok || err != nil {
			t.Fatalf("false trigger %q", text)
		}
	}
}
