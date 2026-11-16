package ai

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
)

func TestRecordedVoiceManifestReplacement(t *testing.T) {
	dir := testVoicePack(t)
	if _, ok, err := recordedVoiceForText(dir, "我回来了"); !ok || err != nil {
		t.Fatalf("initial pack: %v", err)
	}
	path := filepath.Join(dir, "manifest.json")
	stat, _ := os.Stat(path)
	data, _ := os.ReadFile(path)
	var pack recordedVoicePack
	if err := json.Unmarshal(data, &pack); err != nil {
		t.Fatal(err)
	}
	pack.Rules[0].Reply = "等你好久"
	data, _ = json.Marshal(pack)
	if int64(len(data)) != stat.Size() {
		t.Fatal("test needs equal-size replacement")
	}
	next := filepath.Join(dir, "next.json")
	if err := os.WriteFile(next, data, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(next, stat.ModTime(), stat.ModTime()); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(next, path); err != nil {
		t.Fatal(err)
	}
	clip, ok, err := recordedVoiceForText(dir, "我回来了")
	if err != nil || !ok || clip.Reply != "等你好久" {
		t.Fatalf("stale manifest: %#v, %v", clip, err)
	}
	pack.Enabled = false
	data, _ = json.Marshal(pack)
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	if _, ok, err := recordedVoiceForText(dir, "我回来了"); ok || err != nil {
		t.Fatalf("disabled pack matched: %v", err)
	}
}

func TestRecordedVoiceAmbiguityAndParallelLookup(t *testing.T) {
	dir := testVoicePack(t)
	path := filepath.Join(dir, "manifest.json")
	data, _ := os.ReadFile(path)
	var pack recordedVoicePack
	if err := json.Unmarshal(data, &pack); err != nil {
		t.Fatal(err)
	}
	pack.Rules[0].Triggers = append(pack.Rules[0].Triggers, "我回来了！")
	pack.Rules[1].Triggers = append(pack.Rules[1].Triggers, "相同口令")
	pack.Rules = append(pack.Rules, recordedVoiceRule{File: "welcome.pcm", Reply: "其他", Triggers: []string{"相同口令"}})
	data, _ = json.Marshal(pack)
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	var workers sync.WaitGroup
	for range 16 {
		workers.Go(func() {
			if _, ok, err := recordedVoiceForText(dir, "我回来了"); !ok || err != nil {
				t.Errorf("duplicate within rule: %v", err)
			}
			if _, ok, err := recordedVoiceForText(dir, "相同口令"); ok || err == nil {
				t.Error("ambiguous trigger accepted")
			}
		})
	}
	workers.Wait()
}

func BenchmarkRecordedVoiceLookup(b *testing.B) {
	dir := b.TempDir()
	pack := recordedVoicePack{Enabled: true, SampleRate: 24000, Channels: 1, Encoding: "pcm_s16le", Aliases: []string{"达妮娅"}}
	for i := range 221 {
		pack.Rules = append(pack.Rules, recordedVoiceRule{File: "clip.pcm", Reply: "场景录音", Triggers: []string{fmt.Sprintf("播放场景第%d句", i), fmt.Sprintf("场景录音%d", i)}})
	}
	data, _ := json.Marshal(pack)
	if err := os.WriteFile(filepath.Join(dir, "manifest.json"), data, 0600); err != nil {
		b.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "clip.pcm"), []byte{1, 0, 2, 0}, 0600); err != nil {
		b.Fatal(err)
	}
	for _, test := range []struct {
		name, text string
		matched    bool
	}{{"ordinary_chat", "为什么天空是蓝色的", false}, {"recording", "达妮娅，播放场景第220句", true}} {
		b.Run(test.name, func(b *testing.B) {
			b.ReportAllocs()
			for b.Loop() {
				if _, ok, err := recordedVoiceForText(dir, test.text); err != nil || ok != test.matched {
					b.Fatalf("lookup: %v %v", ok, err)
				}
			}
		})
	}
}
