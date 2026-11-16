package ai

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestCodexPromptPreservesOriginalAndLatestIntent(t *testing.T) {
	original := "让 Codex 查一下最近两条 AI 新闻，列出来源链接"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Error(err)
		}
		if _, ok := payload["tools"]; ok {
			t.Error("optimizer must not execute tools")
		}
		messages := payload["messages"].([]any)
		if !strings.Contains(messages[1].(map[string]any)["content"].(string), original) {
			t.Error("verbatim original missing from optimizer input")
		}
		json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{
			"finish_reason": "stop", "message": map[string]any{"content": "检索近期两条人工智能新闻，核对时间并给出来源链接。"}}}})
	}))
	defer server.Close()
	client := newOpenAIClient(server.URL, "test", "test", "", "", "", "")
	prompt, err := client.optimizeCodexRequest(context.Background(), []chatMessage{
		{Role: "user", Content: "之前不要修改机器人文件"},
		{Role: "assistant", Content: "好的"},
		{Role: "user", Content: original}})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(prompt, original) || !strings.Contains(prompt, "以原话为准") ||
		strings.Contains(prompt, "之前不要修改") || strings.Contains(prompt, "不要联网") {
		t.Fatalf("incorrect task prompt: %s", prompt)
	}
}

func TestCodexOptimizerFailureDoesNotSubmitPartialPrompt(t *testing.T) {
	for _, response := range []string{
		`{"choices":[{"message":{"content":""},"finish_reason":"stop"}]}`,
		`{"choices":[{"message":{"content":"truncated"},"finish_reason":"length"}]}`,
		`not json`,
	} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Write([]byte(response))
		}))
		client := newOpenAIClient(server.URL, "test", "test", "", "", "", "")
		prompt, err := client.optimizeCodexRequest(context.Background(), []chatMessage{{Role: "user", Content: "让 Codex 检查文件"}})
		server.Close()
		if err == nil || prompt != "" {
			t.Fatal("invalid optimizer output must not be submitted")
		}
	}
}
