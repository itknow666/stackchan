package ai

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestPCActionArgumentsAndDynamicCapabilities(t *testing.T) {
	c := pcTestCaps()
	for _, test := range []struct {
		action, args string
		valid        bool
	}{
		{"open_app", `{"id":"browser"}`, true},
		{"open_project", `{"id":"robot"}`, true},
		{"set_volume", `{"value":0}`, true},
		{"set_volume", `{"value":100}`, true},
		{"media", `{"command":"next"}`, true},
		{"search_web", `{"query":" 搜索中文 "}`, true},
		{"lock", `{}`, true},
		{"open_app", `{"id":"cmd"}`, false},
		{"open_project", `{"id":"browser"}`, false},
		{"set_volume", `{"value":100.1}`, false},
		{"set_volume", `{"value":-1}`, false},
		{"set_volume", `{"value":true}`, false},
		{"set_volume", `{"value":"30"}`, false},
		{"lock", `{"shell":"anything"}`, false},
		{"media", `{"command":"play"}`, false},
		{"search_web", `{"query":"\nhello"}`, false},
		{"search_web", `{"query":"   "}`, false},
		{"search_web", `{"query":"` + strings.Repeat("中", 301) + `"}`, false},
		{"open_app", `{"path":"C:/anything"}`, false},
		{"shell", `{}`, false},
		{"lock", `null`, false},
	} {
		var args map[string]any
		if err := json.Unmarshal([]byte(test.args), &args); err != nil {
			t.Fatal(err)
		}
		if err := validatePCAction(c, test.action, args); (err == nil) != test.valid {
			t.Errorf("%s %s: err=%v", test.action, test.args, err)
		}
	}
	r, s := newPCTestServer(t)
	control := pcControlFor(pcTestContext(r), r, "robot-allowed")
	if len(control.tools()) != 0 {
		t.Fatal("offline PC advertised tools")
	}
	conn, _, err := pcDial(t, s, pcTestToken)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	limited := pcCapabilities{Apps: []pcItem{{ID: "browser", Name: "Browser"}}, Projects: []pcItem{}, Actions: []string{"open_app"}}
	conn.WriteJSON(pcHello{Type: "hello", Version: 1, PCID: "desktop", Capabilities: limited})
	var ready map[string]any
	if err := conn.ReadJSON(&ready); err != nil {
		t.Fatal(err)
	}
	pcWait(t, func() bool { return r.connected("robot-allowed") != nil })
	tools := control.tools()
	if len(tools) != 1 || tools[0]["function"].(map[string]any)["name"] != "pc_open_app" {
		t.Fatalf("advertised unavailable capability: %v", tools)
	}
	if _, err := control.dispatch(pcTestContext(r), &pcTurn{}, "pc_lock", map[string]any{}); err == nil {
		t.Fatal("unadvertised tool was executed")
	}
	conn.Close()
	pcWait(t, func() bool { return r.connected("robot-allowed") == nil })
	if len(control.tools()) != 0 {
		t.Fatal("disconnected capabilities leaked")
	}
}

func TestPCStrictJSON(t *testing.T) {
	for _, raw := range []string{`{"a":1,"a":2}`, `{"nested":{"a":1,"a":2}}`, `{"a":NaN}`, `{} {}`, `[]`, `"text"`, `{"a":"` + string([]byte{255}) + `"}`} {
		var result map[string]any
		if err := strictPCJSON([]byte(raw), &result); err == nil {
			t.Fatalf("bad JSON accepted: %q", raw)
		}
	}
	var result map[string]any
	if err := strictPCJSON([]byte(`{"nested":{"a":1},"list":[1,{"b":2}]}`), &result); err != nil {
		t.Fatal(err)
	}
}

func TestPCSearchRejectsNonPrintableBeforeRelay(t *testing.T) {
	r, s := newPCTestServer(t)
	conn := pcConnect(t, r, s)
	control := pcControlFor(pcTestContext(r), r, "robot-allowed")
	for _, query := range []string{"query\x7f", "query\u202e", "query\u200b", "query\u00a0", "query\u2028", "query\x00"} {
		if _, err := control.dispatch(pcTestContext(r), &pcTurn{}, "pc_search_web", map[string]any{"query": query}); err == nil {
			t.Fatalf("non-printable query accepted: %q", query)
		}
	}
	for _, query := range []string{"普通中文", "English query", " 中文 English with ASCII spaces "} {
		done := make(chan error, 1)
		go func() {
			_, err := control.dispatch(pcTestContext(r), &pcTurn{}, "pc_search_web", map[string]any{"query": query})
			done <- err
		}()
		// An invalid earlier query would be observed here before this valid one.
		call := pcReadCall(t, conn)
		if call["action"] != "search_web" || call["args"].(map[string]any)["query"] != query {
			t.Fatalf("unexpected query reached the computer: %v", call)
		}
		pcReply(t, conn, call, true, map[string]any{"simulated": true})
		if err := <-done; err != nil {
			t.Fatal(err)
		}
	}
}

func TestPCCodexActionsAreBoundedAndProjectAllowlisted(t *testing.T) {
	caps := pcTestCaps()
	caps.Actions = append(caps.Actions, "codex_start", "codex_status", "codex_cancel")
	for _, test := range []struct {
		action, args string
		valid        bool
	}{
		{"codex_start", `{"project":"robot","task":"检查项目"}`, true},
		{"codex_start", `{"project":"unknown","task":"检查项目"}`, false},
		{"codex_start", `{"project":"robot","task":""}`, false},
		{"codex_start", `{"project":"robot","task":"` + strings.Repeat("中", 4001) + `"}`, false},
		{"codex_start", `{"project":"robot","task":"task","shell":"anything"}`, false},
		{"codex_status", `{"task_id":"latest"}`, true},
		{"codex_status", `{"task_id":"0123456789abcdef0123456789abcdef"}`, true},
		{"codex_cancel", `{"task_id":"--last"}`, false},
		{"codex_cancel", `{"task_id":"latest","force":true}`, false},
	} {
		var args map[string]any
		if err := json.Unmarshal([]byte(test.args), &args); err != nil {
			t.Fatal(err)
		}
		if err := validatePCAction(caps, test.action, args); (err == nil) != test.valid {
			t.Errorf("%s: err=%v", test.action, err)
		}
	}
}
