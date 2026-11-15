/*
SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
SPDX-License-Identifier: MIT
*/

package ai

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/gogf/gf/v2/frame/g"
	"github.com/gogf/gf/v2/os/gctx"
)

type openAIClient struct {
	robot     *robotControl
	baseURL   string
	apiKey    string
	model     string
	sttModel  string
	ttsModel  string
	ttsVoice  string
	sysPrompt string
	http      *http.Client
}

type chatMessage struct {
	Role       string     `json:"role"`
	Content    string     `json:"content,omitempty"`
	ToolCallID string     `json:"tool_call_id,omitempty"`
	ToolCalls  []toolCall `json:"tool_calls,omitempty"`
}

type toolCall struct {
	ID       string `json:"id"`
	Type     string `json:"type"`
	Function struct {
		Name      string `json:"name"`
		Arguments string `json:"arguments"`
	} `json:"function"`
}

func newOpenAIClient(baseURL, apiKey, model, sttModel, ttsModel, ttsVoice, sysPrompt string) *openAIClient {
	baseURL = normalizeOpenAIBaseURL(baseURL)
	if baseURL == "" {
		baseURL = "https://api.openai.com"
	}
	if sttModel == "" {
		sttModel = "whisper-1"
	}
	if ttsModel == "" {
		ttsModel = "tts-1"
	}
	return &openAIClient{
		baseURL:   baseURL,
		apiKey:    apiKey,
		model:     model,
		sttModel:  sttModel,
		ttsModel:  ttsModel,
		ttsVoice:  ttsVoice,
		sysPrompt: sysPrompt,
		http:      &http.Client{},
	}
}

// normalizeOpenAIBaseURL accepts both spellings of a compatible endpoint. The
// settings UI hints at ".../v1" and model discovery appends "/models" to that,
// while the request helpers below add the full "/v1/..." path. Without this
// trim, a base URL copied from the UI hint would produce
// "/v1/v1/audio/transcriptions" and every stage would answer 404.
func normalizeOpenAIBaseURL(baseURL string) string {
	baseURL = strings.TrimRight(strings.TrimSpace(baseURL), "/")
	return strings.TrimSuffix(baseURL, "/v1")
}

func (c *openAIClient) doRequest(ctx context.Context, method, path string, body io.Reader, contentType string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.apiKey)
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 300 {
		return nil, fmt.Errorf("OpenAI %s %s: %s", method, path, string(data))
	}
	return data, nil
}

func (c *openAIClient) usesDeepSeekSpeech() bool {
	u, err := url.Parse(c.baseURL)
	return err == nil && strings.EqualFold(u.Hostname(), "api.deepseek.com")
}

// Transcribe sends WAV audio to the configured speech recognition endpoint.
func (c *openAIClient) Transcribe(ctx context.Context, wavBytes []byte) (string, error) {
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	fw, err := mw.CreateFormFile("file", "audio.wav")
	if err != nil {
		return "", err
	}
	if _, err := fw.Write(wavBytes); err != nil {
		return "", err
	}
	path := "/v1/audio/transcriptions"
	if c.usesDeepSeekSpeech() {
		path = "/v1/tools/asr"
	} else {
		_ = mw.WriteField("model", c.sttModel)
	}
	mw.Close()

	data, err := c.doRequest(ctx, "POST", path, &buf, mw.FormDataContentType())
	if err != nil {
		return "", err
	}
	var resp struct {
		Text string `json:"text"`
	}
	if err := json.Unmarshal(data, &resp); err != nil {
		return "", err
	}
	return resp.Text, nil
}

// Chat sends the conversation history to the model, handles HA tool calls in a loop,
// and returns the final text reply.
func (c *openAIClient) Chat(ctx context.Context, history []chatMessage, ha *haWSClient) (string, error) {
	logCtx := gctx.New()
	now := time.Now().Format("2006-01-02 15:04:05 MST")
	sysContent := fmt.Sprintf("Current date/time: %s\n\n%s", now, c.sysPrompt)

	msgs := make([]chatMessage, 0, len(history)+1)
	msgs = append(msgs, chatMessage{Role: "system", Content: sysContent})
	msgs = append(msgs, history...)
	tools := haOpenAITools(ha)
	if c.robot != nil {
		tools = append(tools, robotToolDefs()...)
	}
	robotCalls := 0
	emotionSet := false

	for round := 0; round < 8; round++ {
		payload := map[string]any{
			"model":    c.model,
			"messages": msgs,
			"tools":    tools,
		}
		if c.robot != nil {
			if !emotionSet {
				payload["tool_choice"] = map[string]any{"type": "function", "function": map[string]string{"name": "set_emotion"}}
			} else if robotCalls >= 3 {
				payload["tool_choice"] = "none"
			}
		}
		if c.usesDeepSeekSpeech() {
			payload["thinking"] = map[string]string{"type": "disabled"}
			payload["max_tokens"] = 512
		}
		body, _ := json.Marshal(payload)
		data, err := c.doRequest(ctx, "POST", "/v1/chat/completions", bytes.NewReader(body), "application/json")
		if err != nil {
			return "", err
		}

		var resp struct {
			Choices []struct {
				Message struct {
					Content   string     `json:"content"`
					ToolCalls []toolCall `json:"tool_calls"`
				} `json:"message"`
			} `json:"choices"`
		}
		if err := json.Unmarshal(data, &resp); err != nil || len(resp.Choices) == 0 {
			return "", fmt.Errorf("unexpected OpenAI response: %s", string(data))
		}

		choice := resp.Choices[0].Message
		if len(choice.ToolCalls) == 0 {
			reply := strings.TrimSpace(choice.Content)
			if c.robot != nil {
				reply = spokenRobotText(reply)
			}
			return reply, nil
		}

		// Execute tool calls and loop for the follow-up reply.
		msgs = append(msgs, chatMessage{Role: "assistant", ToolCalls: choice.ToolCalls})
		for _, tc := range choice.ToolCalls {
			var args map[string]any
			_ = json.Unmarshal([]byte(tc.Function.Arguments), &args)
			g.Log().Infof(logCtx, "[HA] tool=%s args=%v", tc.Function.Name, args)
			var result string
			var dispErr error
			if c.robot != nil && (tc.Function.Name == "set_emotion" || tc.Function.Name == "set_pose") {
				if ctx.Err() != nil {
					return "", ctx.Err()
				}
				if robotCalls >= 3 {
					dispErr = fmt.Errorf("robot action limit reached; answer now")
				} else {
					robotCalls++
					result, dispErr = c.robot.dispatch(tc.Function.Name, args)
					if tc.Function.Name == "set_emotion" && dispErr == nil {
						emotionSet = true
					}
				}
			} else {
				result, dispErr = dispatchHATool(ha, tc.Function.Name, args)
			}
			if dispErr != nil {
				result = "error: " + dispErr.Error()
			}
			g.Log().Infof(logCtx, "[HA] tool=%s result=%s", tc.Function.Name, result)
			msgs = append(msgs, chatMessage{
				Role:       "tool",
				Content:    result,
				ToolCallID: tc.ID,
			})
		}
	}
	return "", fmt.Errorf("chat tool round limit reached")
}

// Speak sends text to OpenAI TTS and returns 24kHz mono int16 PCM.
func (c *openAIClient) Speak(ctx context.Context, text string) ([]int16, error) {
	path := "/v1/audio/speech"
	payload := map[string]any{
		"model":           c.ttsModel,
		"voice":           c.ttsVoice,
		"input":           text,
		"response_format": "pcm",
	}
	if c.usesDeepSeekSpeech() {
		path = "/v1/tools/tts"
		voice := c.ttsVoice
		if voice != "stella" && voice != "tide" {
			voice = "stella"
		}
		payload = map[string]any{"text": text, "voice_id": voice, "format": "pcm", "filter_markdown": true}
	}
	body, _ := json.Marshal(payload)
	data, err := c.doRequest(ctx, "POST", path, bytes.NewReader(body), "application/json")
	if err != nil {
		return nil, err
	}
	if len(data) == 0 || len(data)%2 != 0 {
		return nil, fmt.Errorf("speech endpoint returned empty or incomplete 16-bit PCM")
	}
	pcm := make([]int16, len(data)/2)
	for i := range pcm {
		pcm[i] = int16(binary.LittleEndian.Uint16(data[i*2:]))
	}
	return pcm, nil
}

// haOpenAITools converts the 7 HA tool defs (MCP inputSchema format) to OpenAI function calling format.
func haOpenAITools(ha *haWSClient) []map[string]any {
	if ha == nil {
		return []map[string]any{}
	}
	defs := haToolDefs()
	tools := make([]map[string]any, 0, len(defs))
	for _, t := range defs {
		tools = append(tools, map[string]any{
			"type": "function",
			"function": map[string]any{
				"name":        t["name"],
				"description": t["description"],
				"parameters":  t["inputSchema"],
			},
		})
	}
	return tools
}
