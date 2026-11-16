package ai

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

const pcTestToken = "test-computer-token-not-a-real-secret-1234"
const pcTestGatewayToken = "test-gateway-assertion-not-a-real-secret-5678"

func pcTestContext(r *pcRelay) context.Context {
	req := httptest.NewRequest("GET", "/xiaozhi/ws", nil)
	req.Header.Set("Device-Id", "robot-allowed")
	req.Header.Set(pcGatewayHeader, pcTestGatewayToken)
	return pcRequestContext(context.Background(), r, req)
}

func pcTestCaps() pcCapabilities {
	return pcCapabilities{Apps: []pcItem{{ID: "browser", Name: "Browser"}}, Projects: []pcItem{{ID: "robot", Name: "Robot project"}}, Actions: []string{"open_app", "open_project", "set_volume", "media", "search_web", "lock"}}
}
func newPCTestServer(t *testing.T) (*pcRelay, *httptest.Server) {
	t.Helper()
	r := newPCRelay(pcTestToken, pcTestGatewayToken, "desktop", []string{"robot-allowed"})
	server := httptest.NewServer(r)
	t.Cleanup(func() {
		r.mu.Lock()
		p := r.peer
		r.mu.Unlock()
		if p != nil {
			p.close()
		}
		server.Close()
	})
	return r, server
}
func pcDial(t *testing.T, server *httptest.Server, token string) (*websocket.Conn, *http.Response, error) {
	t.Helper()
	return websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(server.URL, "http"), http.Header{"Authorization": []string{"Bearer " + token}})
}
func pcConnect(t *testing.T, r *pcRelay, server *httptest.Server) *websocket.Conn {
	t.Helper()
	conn, _, err := pcDial(t, server, pcTestToken)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	if err = conn.WriteJSON(pcHello{Type: "hello", Version: 1, PCID: "desktop", Capabilities: pcTestCaps()}); err != nil {
		t.Fatal(err)
	}
	var ready map[string]any
	if err = conn.ReadJSON(&ready); err != nil || ready["type"] != "ready" || ready["version"] != float64(1) || len(ready) != 2 {
		t.Fatalf("ready=%v err=%v", ready, err)
	}
	pcWait(t, func() bool { return r.connected("robot-allowed") != nil })
	return conn
}
func pcWait(t *testing.T, condition func() bool) {
	t.Helper()
	until := time.Now().Add(2 * time.Second)
	for time.Now().Before(until) {
		if condition() {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("condition was not reached")
}
func pcReadCall(t *testing.T, c *websocket.Conn) map[string]any {
	t.Helper()
	_ = c.SetReadDeadline(time.Now().Add(2 * time.Second))
	var call map[string]any
	if err := c.ReadJSON(&call); err != nil {
		t.Fatal(err)
	}
	if call["type"] != "call" || len(call) != 5 || len(call["id"].(string)) != 32 {
		t.Fatalf("invalid call: %v", call)
	}
	remaining := call["deadline_ms"].(float64) - float64(time.Now().UnixMilli())
	if remaining <= 0 || remaining > 15000 {
		t.Fatalf("invalid TTL %v", remaining)
	}
	return call
}
func pcReply(t *testing.T, c *websocket.Conn, call map[string]any, ok bool, data map[string]any) {
	t.Helper()
	if err := c.WriteJSON(map[string]any{"type": "result", "id": call["id"], "ok": ok, "message": "actual computer response", "data": data}); err != nil {
		t.Fatal(err)
	}
}

func TestPCRelayDisabledAndAuthentication(t *testing.T) {
	for _, r := range []*pcRelay{newPCRelay("", pcTestGatewayToken, "desktop", []string{"r"}), newPCRelay("short", pcTestGatewayToken, "desktop", []string{"r"}), newPCRelay(pcTestToken, "", "desktop", []string{"r"}), newPCRelay(pcTestToken, "short", "desktop", []string{"r"}), newPCRelay(pcTestToken, pcTestToken, "desktop", []string{"r"}), newPCRelay(pcTestToken, pcTestGatewayToken, "desktop", nil), newPCRelay(pcTestToken, pcTestGatewayToken, "../invalid", []string{"r"})} {
		rec := httptest.NewRecorder()
		r.ServeHTTP(rec, httptest.NewRequest("GET", "/dania/pc/ws", nil))
		if rec.Code != http.StatusServiceUnavailable {
			t.Fatal(rec.Code)
		}
	}
	r, server := newPCTestServer(t)
	for _, token := range []string{"", pcTestToken + "x", pcTestGatewayToken} {
		_, resp, err := pcDial(t, server, token)
		if err == nil || resp.StatusCode != http.StatusUnauthorized {
			t.Fatalf("authentication accepted: %v", err)
		}
		resp.Body.Close()
	}
	pcConnect(t, r, server)
	if r.connected("untrusted") != nil || pcControlFor(pcTestContext(r), r, "untrusted") != nil {
		t.Fatal("unauthorized device sees capabilities")
	}
	if _, err := r.call(pcTestContext(r), "untrusted", "lock", map[string]any{}); err == nil {
		t.Fatal("unauthorized dispatch succeeded")
	}
	var forged = &pcControl{relay: r, deviceID: "untrusted"}
	if len(forged.tools()) != 0 {
		t.Fatal("forged control disclosed tools")
	}
	if _, err := forged.dispatch(pcTestContext(r), &pcTurn{}, "pc_lock", map[string]any{}); err == nil {
		t.Fatal("dispatch missed authorization")
	}
}

func TestPCRelayHelloValidationAndTakeover(t *testing.T) {
	bad := []string{
		`{"type":"hello","version":1,"pc_id":"other","capabilities":{"apps":[],"projects":[],"actions":[]}}`,
		`{"Type":"hello","version":1,"pc_id":"desktop","capabilities":{"apps":[],"projects":[],"actions":[]}}`,
		`{"type":"hello","version":1,"pc_id":"desktop","capabilities":{"apps":[{"ID":"b","name":"B"}],"projects":[],"actions":[]}}`,
		`{"type":"hello","version":1,"pc_id":"desktop","capabilities":{"apps":[],"projects":[],"actions":["shell"]}}`,
		`{"type":"hello","version":1,"pc_id":"desktop","capabilities":{"apps":[],"projects":[],"actions":["lock","lock"]}}`,
		`{"type":"hello","version":1,"pc_id":"desktop","capabilities":{"apps":[{"id":"b","name":"B"},{"id":"b","name":"B"}],"projects":[],"actions":[]}}`,
		`{"type":"hello","version":1,"pc_id":"desktop","pc_id":"desktop","capabilities":{"apps":[],"projects":[],"actions":[]}}`,
		`{"type":"hello","version":1,"pc_id":"desktop","capabilities":{"apps":[],"projects":[],"actions":[]},"extra":true}`,
	}
	for i, raw := range bad {
		t.Run(fmt.Sprint(i), func(t *testing.T) {
			r, s := newPCTestServer(t)
			c, _, err := pcDial(t, s, pcTestToken)
			if err != nil {
				t.Fatal(err)
			}
			defer c.Close()
			c.WriteMessage(websocket.TextMessage, []byte(raw))
			c.SetReadDeadline(time.Now().Add(time.Second))
			if _, _, err = c.ReadMessage(); err == nil {
				t.Fatal("bad hello accepted")
			}
			if r.connected("robot-allowed") != nil {
				t.Fatal("bad peer registered")
			}
		})
	}
	r, s := newPCTestServer(t)
	first := pcConnect(t, r, s)
	second, _, err := pcDial(t, s, pcTestToken)
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	second.WriteJSON(pcHello{Type: "hello", Version: 1, PCID: "desktop", Capabilities: pcTestCaps()})
	second.SetReadDeadline(time.Now().Add(time.Second))
	if _, _, err = second.ReadMessage(); err == nil {
		t.Fatal("takeover accepted")
	}
	done := make(chan error, 1)
	go func() { _, err := r.call(pcTestContext(r), "robot-allowed", "lock", map[string]any{}); done <- err }()
	call := pcReadCall(t, first)
	pcReply(t, first, call, true, map[string]any{})
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}

func TestPCRelayActualResultAndBoundedPending(t *testing.T) {
	r, s := newPCTestServer(t)
	c := pcConnect(t, r, s)
	done := make(chan pcOutcome, 4)
	for i := 0; i < 4; i++ {
		go func() {
			result, err := r.call(pcTestContext(r), "robot-allowed", "lock", map[string]any{})
			done <- pcOutcome{result: result, err: err}
		}()
	}
	calls := make([]map[string]any, 4)
	for i := range calls {
		calls[i] = pcReadCall(t, c)
	}
	if _, err := r.call(pcTestContext(r), "robot-allowed", "lock", map[string]any{}); err == nil || !strings.Contains(err.Error(), "busy") {
		t.Fatalf("pending cap: %v", err)
	}
	select {
	case <-done:
		t.Fatal("success returned before acknowledgement")
	default:
	}
	for i, call := range calls {
		pcReply(t, c, call, i%2 == 0, map[string]any{"simulated": true, "value": 37})
	}
	seenFalse := false
	for range calls {
		out := <-done
		if out.err != nil || out.result.OK == nil || out.result.Data["simulated"] != true || out.result.Data["value"] != float64(37) {
			t.Fatalf("actual result lost: %+v", out)
		}
		if !*out.result.OK {
			seenFalse = true
		}
	}
	if !seenFalse {
		t.Fatal("failed PC result was turned into success")
	}
}

func TestPCRelayDisconnectReconnectAndUnsolicited(t *testing.T) {
	r, s := newPCTestServer(t)
	c := pcConnect(t, r, s)
	done := make(chan error, 1)
	go func() { _, err := r.call(pcTestContext(r), "robot-allowed", "lock", map[string]any{}); done <- err }()
	oldCall := pcReadCall(t, c)
	c.Close()
	if err := <-done; err == nil || !strings.Contains(err.Error(), "not retried") {
		t.Fatalf("pending not failed on disconnect: %v", err)
	}
	pcWait(t, func() bool { return r.connected("robot-allowed") == nil })
	next := pcConnect(t, r, s)
	// A result from the old socket cannot complete work on a new connection.
	pcReply(t, next, oldCall, true, map[string]any{})
	pcWait(t, func() bool { return r.connected("robot-allowed") == nil })
	if _, err := r.call(pcTestContext(r), "robot-allowed", "lock", map[string]any{}); err == nil || !strings.Contains(err.Error(), "offline") {
		t.Fatal(err)
	}
}

func TestPCRelayTimeoutAndCancellation(t *testing.T) {
	r, s := newPCTestServer(t)
	r.timeout = 150 * time.Millisecond
	c := pcConnect(t, r, s)
	done := make(chan error, 1)
	go func() { _, err := r.call(pcTestContext(r), "robot-allowed", "lock", map[string]any{}); done <- err }()
	pcReadCall(t, c)
	if err := <-done; err == nil || !strings.Contains(err.Error(), "timed out") {
		t.Fatalf("missing timeout: %v", err)
	}
	p := r.connected("robot-allowed")
	p.mu.Lock()
	count := len(p.pending)
	p.mu.Unlock()
	if count != 0 {
		t.Fatal("timeout leaked pending")
	}
	ctx, cancel := context.WithCancel(pcTestContext(r))
	cancel()
	if _, err := r.call(ctx, "robot-allowed", "lock", map[string]any{}); err != context.Canceled {
		t.Fatalf("cancelled call: %v", err)
	}
}

func TestPCRelayCancelledWriterDoesNotSendQueuedAction(t *testing.T) {
	r, s := newPCTestServer(t)
	conn := pcConnect(t, r, s)
	p := r.connected("robot-allowed")
	p.writeMu.Lock()
	ctx, cancel := context.WithCancel(pcTestContext(r))
	defer cancel()
	done := make(chan error, 1)
	go func() { _, err := r.call(ctx, "robot-allowed", "lock", map[string]any{}); done <- err }()
	pcWait(t, func() bool { p.mu.Lock(); defer p.mu.Unlock(); return len(p.pending) == 1 })
	cancel()
	p.writeMu.Unlock()
	if err := <-done; err == nil {
		t.Fatal("cancelled writer reported success")
	}
	conn.SetReadDeadline(time.Now().Add(time.Second))
	if _, _, err := conn.ReadMessage(); err == nil {
		t.Fatal("cancelled queued action reached computer")
	}
}

func TestPCRelayRejectsMalformedResults(t *testing.T) {
	for _, bad := range []string{`{"type":"result","id":"ID","ok":true,"data":{}}`, `{"type":"result","id":"ID","ok":"true","message":"x","data":{}}`, `{"type":"result","id":"ID","ok":true,"message":"x","data":null}`, `{"type":"result","id":"ID","ok":true,"ok":false,"message":"x","data":{}}`, strings.Repeat("x", pcMaxFrame+1)} {
		r, s := newPCTestServer(t)
		c := pcConnect(t, r, s)
		done := make(chan error, 1)
		go func() { _, err := r.call(pcTestContext(r), "robot-allowed", "lock", map[string]any{}); done <- err }()
		call := pcReadCall(t, c)
		c.WriteMessage(websocket.TextMessage, []byte(strings.ReplaceAll(bad, "ID", call["id"].(string))))
		select {
		case err := <-done:
			if err == nil {
				t.Fatal("malformed result completed pending")
			}
		case <-time.After(2 * time.Second):
			t.Fatal("pending not cleaned up")
		}
	}
}

func TestPCRelayConcurrentConnectionLifecycle(t *testing.T) {
	r, s := newPCTestServer(t)
	for i := 0; i < 6; i++ {
		c := pcConnect(t, r, s)
		var wg sync.WaitGroup
		for j := 0; j < 8; j++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				ctx, cancel := context.WithTimeout(pcTestContext(r), 50*time.Millisecond)
				defer cancel()
				r.call(ctx, "robot-allowed", "lock", map[string]any{})
				pcControlFor(pcTestContext(r), r, "robot-allowed").tools()
			}()
		}
		c.Close()
		wg.Wait()
		pcWait(t, func() bool { return r.connected("robot-allowed") == nil })
	}
}

func TestPCRelayHandshakeAndHeartbeatDeadlines(t *testing.T) {
	r, s := newPCTestServer(t)
	r.helloTimeout = 40 * time.Millisecond
	c, _, err := pcDial(t, s, pcTestToken)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	c.SetReadDeadline(time.Now().Add(time.Second))
	if _, _, err = c.ReadMessage(); err == nil {
		t.Fatal("silent handshake did not expire")
	}
	for _, answerPings := range []bool{true, false} {
		t.Run(fmt.Sprint(answerPings), func(t *testing.T) {
			r, s := newPCTestServer(t)
			r.pingInterval = 25 * time.Millisecond
			r.pongTimeout = 150 * time.Millisecond
			conn := pcConnect(t, r, s)
			var pings atomic.Int32
			conn.SetPingHandler(func(payload string) error {
				pings.Add(1)
				if answerPings {
					return conn.WriteControl(websocket.PongMessage, []byte(payload), time.Now().Add(time.Second))
				}
				return nil
			})
			done := make(chan struct{})
			go func() {
				defer close(done)
				for {
					if _, _, err := conn.ReadMessage(); err != nil {
						return
					}
				}
			}()
			if answerPings {
				pcWait(t, func() bool { return pings.Load() >= 8 })
				if r.connected("robot-allowed") == nil {
					t.Fatal("valid pongs did not keep connection alive")
				}
			} else {
				pcWait(t, func() bool { return r.connected("robot-allowed") == nil })
				if pings.Load() == 0 {
					t.Fatal("server never sent ping")
				}
			}
			conn.Close()
			<-done
		})
	}
}

func TestPCRelayRejectsBinaryFrames(t *testing.T) {
	r, s := newPCTestServer(t)
	conn, _, err := pcDial(t, s, pcTestToken)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(pcHello{Type: "hello", Version: 1, PCID: "desktop", Capabilities: pcTestCaps()})
	conn.WriteMessage(websocket.BinaryMessage, raw)
	conn.SetReadDeadline(time.Now().Add(time.Second))
	if _, _, err = conn.ReadMessage(); err == nil {
		t.Fatal("binary hello accepted")
	}
	conn = pcConnect(t, r, s)
	done := make(chan error, 1)
	go func() { _, err := r.call(pcTestContext(r), "robot-allowed", "lock", map[string]any{}); done <- err }()
	call := pcReadCall(t, conn)
	raw, _ = json.Marshal(map[string]any{"type": "result", "id": call["id"], "ok": true, "message": "x", "data": map[string]any{}})
	conn.WriteMessage(websocket.BinaryMessage, raw)
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("binary result accepted")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("binary result left call pending")
	}
}

func TestPCProviderOnlyAttachesAuthorizedControl(t *testing.T) {
	t.Setenv("STACKCHAN_DATA_DIR", t.TempDir())
	if err := writeSettings(map[string]string{"provider": "openai_compatible", "compatible_base_url": "https://example.invalid", "compatible_api_key": "test-key", "compatible_model": "test-model"}); err != nil {
		t.Fatal(err)
	}
	old := defaultPCRelay
	defaultPCRelay = newPCRelay(pcTestToken, pcTestGatewayToken, "desktop", []string{"robot-allowed"})
	defer func() { defaultPCRelay = old }()
	for _, id := range []string{"robot-allowed", "other"} {
		session, err := dialProvider(pcTestContext(defaultPCRelay), id, nil, RealtimeCallbacks{})
		if err != nil {
			t.Fatal(err)
		}
		s := session.(*compatibleSession)
		if (s.llmClient.pc != nil) != (id == "robot-allowed") {
			t.Fatal("provider authorization failed")
		}
		s.Close()
	}
}

func TestPCChatBudgetMemoizationAndRobotBudget(t *testing.T) {
	r, s := newPCTestServer(t)
	conn := pcConnect(t, r, s)
	count := make(chan int, 1)
	go func() {
		n := 0
		for {
			var call map[string]any
			if conn.ReadJSON(&call) != nil {
				count <- n
				return
			}
			n++
			if conn.WriteJSON(map[string]any{"type": "result", "id": call["id"], "ok": true, "message": "simulated, no effect", "data": map[string]any{"simulated": true}}) != nil {
				count <- n
				return
			}
		}
	}()
	c := newOpenAIClient("https://example.invalid", "test", "model", "", "", "", "")
	c.pc = pcControlFor(pcTestContext(r), r, "robot-allowed")
	c.robot = &robotControl{emotion: func(string) error { return nil }, pose: func(RobotPose) error { return nil }}
	round := 0
	c.http = &http.Client{Transport: deepSeekTestTransport(func(req *http.Request) (*http.Response, error) {
		round++
		var payload map[string]any
		if err := json.NewDecoder(req.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		var tools []string
		for _, tool := range payload["tools"].([]any) {
			tools = append(tools, tool.(map[string]any)["function"].(map[string]any)["name"].(string))
		}
		if round == 1 {
			if payload["tool_choice"].(map[string]any)["function"].(map[string]any)["name"] != "set_emotion" {
				t.Fatal("first emotion not forced")
			}
		}
		if round >= 3 {
			for _, tool := range tools {
				if tool == "set_pose" || tool == "set_emotion" {
					t.Fatal("robot tools remain after their budget")
				}
			}
			if round == 3 && !strings.Contains(strings.Join(tools, ","), "pc_media") {
				t.Fatal("robot budget hid PC tools")
			}
			if payload["tool_choice"] == "none" {
				t.Fatal("global tool choice disabled PC")
			}
		}
		call := func(id, name, args string) map[string]any {
			return map[string]any{"id": id, "type": "function", "function": map[string]any{"name": name, "arguments": args}}
		}
		message := map[string]any{}
		switch round {
		case 1:
			message["tool_calls"] = []any{call("emotion", "set_emotion", `{"emotion":"HOT"}`)}
		case 2:
			message["tool_calls"] = []any{call("pose1", "set_pose", `{"yaw":0,"pitch":0,"roll":0}`), call("pose2", "set_pose", `{"yaw":0,"pitch":0,"roll":0}`)}
		case 3:
			message["tool_calls"] = []any{call("media1", "pc_media", `{"command":"play_pause"}`), call("media2", "pc_media", `{ "command" : "play_pause" }`), call("vol1", "pc_set_volume", `{"value":25}`), call("vol2", "pc_set_volume", `{"value":30}`), call("vol3", "pc_set_volume", `{"value":35}`), call("over", "pc_lock", `{}`)}
		default:
			if strings.Contains(strings.Join(tools, ","), "pc_") {
				t.Fatal("PC tools remain after their budget")
			}
			messages := payload["messages"].([]any)
			var mediaResults []string
			foundLimit := false
			for _, raw := range messages {
				msg := raw.(map[string]any)
				id, _ := msg["tool_call_id"].(string)
				if id == "media1" || id == "media2" {
					mediaResults = append(mediaResults, msg["content"].(string))
				}
				if id == "over" {
					foundLimit = strings.Contains(msg["content"].(string), "limit")
				}
			}
			if len(mediaResults) != 2 || mediaResults[0] != mediaResults[1] || !strings.Contains(mediaResults[0], `"simulated":true`) || !foundLimit {
				t.Fatalf("bad tool results: %v", mediaResults)
			}
			message["content"] = "Only simulated"
		}
		data, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"message": message}}})
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(string(data))), Header: make(http.Header)}, nil
	})}
	if _, err := c.Chat(pcTestContext(r), nil, nil); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	if n := <-count; n != 4 {
		t.Fatalf("expected four effects, got %d", n)
	}
}
