package ai

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"unicode"
)

type recordedVoiceRule struct {
	File     string   `json:"file"`
	Reply    string   `json:"reply"`
	Triggers []string `json:"triggers"`
}

type recordedVoicePack struct {
	Enabled    bool                `json:"enabled"`
	SampleRate int                 `json:"sample_rate"`
	Channels   int                 `json:"channels"`
	Encoding   string              `json:"encoding"`
	Aliases    []string            `json:"aliases"`
	Rules      []recordedVoiceRule `json:"rules"`
}

type recordedVoice struct {
	Reply string
	PCM   []int16
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
	data, err := os.ReadFile(filepath.Join(dir, "manifest.json"))
	if err != nil {
		return recordedVoice{}, false, err
	}
	if len(data) > 128*1024 {
		return recordedVoice{}, false, fmt.Errorf("voice manifest exceeds 128 KiB")
	}
	var pack recordedVoicePack
	if err := json.Unmarshal(data, &pack); err != nil {
		return recordedVoice{}, false, fmt.Errorf("invalid voice manifest")
	}
	if !pack.Enabled {
		return recordedVoice{}, false, nil
	}
	if pack.SampleRate != serverSampleRate || pack.Channels != 1 || pack.Encoding != "pcm_s16le" {
		return recordedVoice{}, false, fmt.Errorf("voice pack must use 24 kHz mono PCM16")
	}
	phrase := normalizeVoicePhrase(text)
	for _, alias := range pack.Aliases {
		if prefix := normalizeVoicePhrase(alias); prefix != "" && strings.HasPrefix(phrase, prefix) {
			phrase = strings.TrimPrefix(phrase, prefix)
			break
		}
	}
	if phrase == "" {
		return recordedVoice{}, false, nil
	}
	var matched *recordedVoiceRule
	for i := range pack.Rules {
		for _, trigger := range pack.Rules[i].Triggers {
			if normalizeVoicePhrase(trigger) == phrase {
				if matched != nil && matched != &pack.Rules[i] {
					return recordedVoice{}, false, fmt.Errorf("ambiguous recorded voice trigger")
				}
				matched = &pack.Rules[i]
			}
		}
	}
	if matched == nil {
		return recordedVoice{}, false, nil
	}
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
	data, err = os.ReadFile(path)
	if err != nil {
		return recordedVoice{}, false, fmt.Errorf("recorded voice file could not be read")
	}
	pcm := make([]int16, len(data)/2)
	for i := range pcm {
		pcm[i] = int16(binary.LittleEndian.Uint16(data[i*2:]))
	}
	return recordedVoice{Reply: matched.Reply, PCM: pcm}, true, nil
}
