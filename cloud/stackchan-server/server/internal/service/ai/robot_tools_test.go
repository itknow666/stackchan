package ai

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"reflect"
	"strings"
	"testing"
)

func TestRobotToolsBeforeSpeech(t *testing.T) {
	c := newOpenAIClient("https://api.deepseek.com", "test", "deepseek-flash", "", "", "", "prompt")
	var events []string
	c.robot = &robotControl{emotion: func(e string) error { events = append(events, e); return nil }, pose: func(p RobotPose) error {
		if p.Pitch != .25 || p.Roll != 0 {
			t.Fatalf("bad pose: %+v", p)
		}
		events = append(events, "pose")
		return nil
	}}
	round := 0
	c.http = &http.Client{Transport: deepSeekTestTransport(func(r *http.Request) (*http.Response, error) {
		round++
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		if round == 1 {
			if payload["tool_choice"].(map[string]any)["function"].(map[string]any)["name"] != "set_emotion" {
				t.Fatal("emotion must precede speech")
			}
		}
		var message map[string]any
		switch round {
		case 1:
			message = map[string]any{"tool_calls": []map[string]any{{"id": "e", "type": "function", "function": map[string]any{"name": "set_emotion", "arguments": `{"emotion":"HOT"}`}}}}
		case 2:
			message = map[string]any{"tool_calls": []map[string]any{{"id": "p", "type": "function", "function": map[string]any{"name": "set_pose", "arguments": `{"yaw":0,"pitch":0.25,"roll":0}`}}}}
		default:
			message = map[string]any{"content": "哼 **算你有眼光** 😊"}
		}
		data, _ := json.Marshal(map[string]any{"choices": []map[string]any{{"message": message}}})
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(string(data))), Header: make(http.Header)}, nil
	})}
	reply, err := c.Chat(context.Background(), []chatMessage{{Role: "user", Content: "你真厉害"}}, nil)
	if err != nil || reply != "哼 算你有眼光" || !reflect.DeepEqual(events, []string{"hot", "pose"}) {
		t.Fatalf("reply=%q events=%v err=%v", reply, events, err)
	}
}

func TestRobotToolsRejectUnsafeOrUnsupportedActions(t *testing.T) {
	calls := 0
	r := &robotControl{emotion: func(string) error { calls++; return nil }, pose: func(RobotPose) error { calls++; return nil }}
	for _, args := range []map[string]any{
		{"yaw": .6, "pitch": 0.0, "roll": 0.0},
		{"yaw": 0.0, "pitch": .4, "roll": 0.0},
		{"yaw": 0.0, "pitch": 0.0, "roll": .25},
		{"yaw": 0.0, "pitch": 0.0},
	} {
		if _, err := r.dispatch("set_pose", args); err == nil {
			t.Fatal("unsafe pose accepted")
		}
	}
	if _, err := r.dispatch("set_emotion", map[string]any{"emotion": "SURPRISED"}); err == nil {
		t.Fatal("unsupported emotion accepted")
	}
	if calls != 0 {
		t.Fatal("invalid actions reached hardware")
	}
}
