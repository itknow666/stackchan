package ai

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
)

type deepSeekTestTransport func(*http.Request) (*http.Response, error)

func (f deepSeekTestTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestDeepSeekSpeechContracts(t *testing.T) {
	c := newOpenAIClient("https://api.deepseek.com/v1", "test-key", "deepseek-flash", "asr", "tts", "stella", "Reply briefly")
	c.http = &http.Client{Transport: deepSeekTestTransport(func(r *http.Request) (*http.Response, error) {
		if r.Header.Get("Authorization") != "Bearer test-key" {
			t.Fatal("missing API authentication")
		}
		var body []byte
		switch r.URL.Path {
		case "/v1/tools/asr":
			if err := r.ParseMultipartForm(1024); err != nil {
				t.Fatal(err)
			}
			defer r.MultipartForm.RemoveAll()
			f, _, err := r.FormFile("file")
			if err != nil {
				t.Fatal(err)
			}
			defer f.Close()
			data, _ := io.ReadAll(f)
			if !bytes.Equal(data, []byte("test-wav")) || r.FormValue("model") != "" {
				t.Fatal("incorrect DeepSeek ASR payload")
			}
			body = []byte(`{"text":"你好","duration_ms":1000}`)
		case "/v1/tools/tts":
			var p map[string]any
			if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
				t.Fatal(err)
			}
			if p["text"] != "你好" || p["voice_id"] != "stella" || p["format"] != "pcm" || p["filter_markdown"] != true || len(p) != 4 {
				t.Fatalf("incorrect TTS payload: %v", p)
			}
			body = []byte{1, 0, 255, 255}
		case "/v1/chat/completions":
			var p map[string]any
			if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
				t.Fatal(err)
			}
			thinking, _ := p["thinking"].(map[string]any)
			if p["model"] != "deepseek-flash" || thinking["type"] != "disabled" {
				t.Fatalf("incorrect voice chat configuration: %v", p)
			}
			body = []byte(`{"choices":[{"message":{"content":"你好"}}]}`)
		default:
			t.Fatalf("unexpected API path: %s", r.URL.Path)
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(body)), Header: make(http.Header)}, nil
	})}
	text, err := c.Transcribe(context.Background(), []byte("test-wav"))
	if err != nil || text != "你好" {
		t.Fatalf("transcription = %q, %v", text, err)
	}
	text, err = c.Chat(context.Background(), []chatMessage{{Role: "user", Content: "你好"}}, nil)
	if err != nil || text != "你好" {
		t.Fatalf("chat = %q, %v", text, err)
	}
	pcm, err := c.Speak(context.Background(), "你好")
	if err != nil || len(pcm) != 2 || pcm[0] != 1 || pcm[1] != -1 {
		t.Fatalf("PCM = %v, %v", pcm, err)
	}
}

func TestDeepSeekDetectionAndInvalidAudio(t *testing.T) {
	for _, endpoint := range []string{"https://api.deepseek.com.evil.test", "https://other.test", "https://deepseek.com"} {
		if newOpenAIClient(endpoint, "", "", "", "", "", "").usesDeepSeekSpeech() {
			t.Fatalf("incorrect provider detection: %s", endpoint)
		}
	}
	c := newOpenAIClient("https://api.deepseek.com", "key", "", "", "", "alloy", "")
	for _, payload := range []string{"", "x"} {
		c.http = &http.Client{Transport: deepSeekTestTransport(func(r *http.Request) (*http.Response, error) {
			var p map[string]any
			_ = json.NewDecoder(r.Body).Decode(&p)
			if p["voice_id"] != "stella" {
				t.Fatal("default voice must be supported by DeepSeek")
			}
			return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(payload)), Header: make(http.Header)}, nil
		})}
		if _, err := c.Speak(context.Background(), "hi"); err == nil {
			t.Fatal("incomplete PCM accepted")
		}
	}
}
