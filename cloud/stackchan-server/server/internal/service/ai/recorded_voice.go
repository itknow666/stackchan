package ai

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"unicode"
)

type recordedVoiceRule struct {
	ID       string   `json:"id,omitempty"`
	File     string   `json:"file"`
	Reply    string   `json:"reply"`
	Triggers []string `json:"triggers"`
}

type recordedVoicePack struct {
	PackID     string              `json:"pack_id,omitempty"`
	Enabled    bool                `json:"enabled"`
	SampleRate int                 `json:"sample_rate"`
	Channels   int                 `json:"channels"`
	Encoding   string              `json:"encoding"`
	Aliases    []string            `json:"aliases"`
	Rules      []recordedVoiceRule `json:"rules"`
}

type recordedVoice struct {
	PackID, ID string
	Reply      string
	PCM        []int16
}

type recordedVoiceIndex struct {
	pack    recordedVoicePack
	aliases []string
	rules   map[string]int
}

// Cache only the active manifest. File identity also detects atomic replacement
// when a new manifest retains the old modification time and size.
var voiceManifestCache struct {
	sync.Mutex
	path  string
	stat  os.FileInfo
	index *recordedVoiceIndex
}

func loadRecordedVoiceIndex(dir string) (*recordedVoiceIndex, error) {
	path := filepath.Join(dir, "manifest.json")
	voiceManifestCache.Lock()
	defer voiceManifestCache.Unlock()
	stat, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if voiceManifestCache.path == path && voiceManifestCache.stat != nil &&
		os.SameFile(stat, voiceManifestCache.stat) && stat.Size() == voiceManifestCache.stat.Size() &&
		stat.ModTime().Equal(voiceManifestCache.stat.ModTime()) {
		return voiceManifestCache.index, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(data) > 128*1024 {
		return nil, fmt.Errorf("voice manifest exceeds 128 KiB")
	}
	index := &recordedVoiceIndex{rules: make(map[string]int)}
	if err := json.Unmarshal(data, &index.pack); err != nil {
		return nil, fmt.Errorf("invalid voice manifest")
	}
	for _, alias := range index.pack.Aliases {
		if alias = normalizeVoicePhrase(alias); alias != "" {
			index.aliases = append(index.aliases, alias)
		}
	}
	for i, rule := range index.pack.Rules {
		for _, trigger := range rule.Triggers {
			phrase := normalizeVoicePhrase(trigger)
			if previous, exists := index.rules[phrase]; exists && previous != i {
				index.rules[phrase] = -1
			} else if !exists {
				index.rules[phrase] = i
			}
		}
	}
	voiceManifestCache.path, voiceManifestCache.stat, voiceManifestCache.index = path, stat, index
	return index, nil
}

func normalizeVoicePhrase(text string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsSpace(r) || unicode.IsPunct(r) {
			return -1
		}
		return unicode.ToLower(r)
	}, text)
}

// Matching is whole-utterance only. In particular, "我没有成功" must not
// accidentally match a positive phrase such as "成功" inside a sentence.
func recordedVoiceForText(dir, text string) (recordedVoice, bool, error) {
	if dir == "" {
		return recordedVoice{}, false, nil
	}
	index, err := loadRecordedVoiceIndex(dir)
	if err != nil {
		return recordedVoice{}, false, err
	}
	pack := &index.pack
	if !pack.Enabled {
		return recordedVoice{}, false, nil
	}
	if pack.SampleRate != serverSampleRate || pack.Channels != 1 || pack.Encoding != "pcm_s16le" {
		return recordedVoice{}, false, fmt.Errorf("voice pack must use 24 kHz mono PCM16")
	}
	phrase := normalizeVoicePhrase(text)
	for _, prefix := range index.aliases {
		if strings.HasPrefix(phrase, prefix) {
			phrase = strings.TrimPrefix(phrase, prefix)
			break
		}
	}
	if phrase == "" {
		return recordedVoice{}, false, nil
	}
	rule, exists := index.rules[phrase]
	if !exists {
		return recordedVoice{}, false, nil
	}
	if rule < 0 {
		return recordedVoice{}, false, fmt.Errorf("ambiguous recorded voice trigger")
	}
	matched := &pack.Rules[rule]
	if matched.File == "" || filepath.Base(matched.File) != matched.File || strings.ContainsAny(matched.File, "/\\") || filepath.Ext(matched.File) != ".pcm" || strings.TrimSpace(matched.Reply) == "" {
		return recordedVoice{}, false, fmt.Errorf("invalid recorded voice entry")
	}
	// The pack is administrator-managed, but still refuse paths escaping it.
	root, err := filepath.EvalSymlinks(dir)
	if err != nil {
		return recordedVoice{}, false, err
	}
	path, err := filepath.EvalSymlinks(filepath.Join(root, matched.File))
	if err != nil {
		return recordedVoice{}, false, fmt.Errorf("recorded voice file unavailable")
	}
	if filepath.Dir(path) != root {
		return recordedVoice{}, false, fmt.Errorf("recorded voice path escapes pack directory")
	}
	stat, err := os.Stat(path)
	if err != nil || !stat.Mode().IsRegular() || stat.Size() == 0 || stat.Size()%2 != 0 || stat.Size() > serverSampleRate*2*15 {
		return recordedVoice{}, false, fmt.Errorf("recorded voice must be valid PCM under 15 seconds")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return recordedVoice{}, false, fmt.Errorf("recorded voice file could not be read")
	}
	pcm := make([]int16, len(data)/2)
	for i := range pcm {
		pcm[i] = int16(binary.LittleEndian.Uint16(data[i*2:]))
	}
	clip := recordedVoice{Reply: matched.Reply, PCM: pcm}
	// Metadata is optional. Old manifests and invalid optional metadata retain
	// the original network PCM behavior rather than losing the recording.
	if validLocalAudioID(pack.PackID) && validLocalAudioID(matched.ID) {
		clip.PackID, clip.ID = pack.PackID, matched.ID
	}
	return clip, true, nil
}
