package ai

import (
	"context"
	"net/http/httptest"
	"testing"
)

func TestPCGatewayAssertionGatesProviderAndDispatch(t *testing.T) {
	t.Setenv("STACKCHAN_DATA_DIR", t.TempDir())
	if err := writeSettings(map[string]string{"provider": "openai_compatible", "compatible_base_url": "https://example.invalid", "compatible_api_key": "test-key", "compatible_model": "test-model"}); err != nil {
		t.Fatal(err)
	}
	r, server := newPCTestServer(t)
	conn := pcConnect(t, r, server)
	old := defaultPCRelay
	defaultPCRelay = r
	defer func() { defaultPCRelay = old }()
	trusted := pcTestContext(r)
	control := pcControlFor(trusted, r, "robot-allowed")
	if control == nil || len(control.tools()) == 0 {
		t.Fatal("valid gateway assertion did not authorize robot")
	}
	for _, test := range []struct {
		name, device, assertion string
		duplicate               bool
	}{
		{"device-id-alone", "robot-allowed", "", false},
		{"forged-device-without-proof", "other", "", false},
		{"wrong-assertion", "robot-allowed", "wrong", false},
		{"pc-token-is-not-gateway-proof", "robot-allowed", pcTestToken, false},
		{"gateway-proof-does-not-authorize-other-device", "other", pcTestGatewayToken, false},
		{"multiple-assertion-headers", "robot-allowed", pcTestGatewayToken, true},
	} {
		t.Run(test.name, func(t *testing.T) {
			req := httptest.NewRequest("GET", "/xiaozhi/ws", nil)
			req.Header.Set("Device-Id", test.device)
			req.Header.Set("Authorization", "Bearer "+pcTestToken)
			if test.assertion != "" {
				req.Header.Set(pcGatewayHeader, test.assertion)
			}
			if test.duplicate {
				req.Header.Add(pcGatewayHeader, pcTestGatewayToken)
			}
			// Even a mistakenly inherited grant from another request must be revoked.
			ctx := pcRequestContext(trusted, r, req)
			if r.authorizedContext(ctx, test.device) || pcControlFor(ctx, r, test.device) != nil {
				t.Fatal("untrusted request obtained computer capability")
			}
			session, err := dialProvider(ctx, test.device, nil, RealtimeCallbacks{})
			if err != nil {
				t.Fatalf("ordinary voice session was rejected: %v", err)
			}
			if session.(*compatibleSession).llmClient.pc != nil {
				t.Fatal("provider exposed PC tools to untrusted request")
			}
			session.Close()
			if _, err := control.dispatch(ctx, &pcTurn{}, "pc_lock", map[string]any{}); err == nil {
				t.Fatal("dispatch accepted a control copied from trusted request")
			}
			if _, err := r.call(ctx, "robot-allowed", "lock", map[string]any{}); err == nil {
				t.Fatal("direct relay dispatch bypassed request authorization")
			}
		})
	}
	if pcControlFor(context.Background(), r, "robot-allowed") != nil {
		t.Fatal("request authorization leaked into a later connection")
	}
	child, cancel := context.WithCancel(trusted)
	defer cancel()
	session, err := dialProvider(child, "robot-allowed", nil, RealtimeCallbacks{})
	if err != nil {
		t.Fatal(err)
	}
	if session.(*compatibleSession).llmClient.pc == nil {
		t.Fatal("trusted request lost authorization in child context")
	}
	session.Close()
	done := make(chan error, 1)
	go func() { _, err := control.dispatch(child, &pcTurn{}, "pc_lock", map[string]any{}); done <- err }()
	call := pcReadCall(t, conn)
	pcReply(t, conn, call, true, map[string]any{"simulated": true})
	if err := <-done; err != nil {
		t.Fatalf("trusted request failed: %v", err)
	}
}

func TestPCGatewayConfigurationIsFailClosed(t *testing.T) {
	t.Setenv("STACKCHAN_PC_TOKEN", pcTestToken)
	t.Setenv("STACKCHAN_PC_ID", "desktop")
	t.Setenv("STACKCHAN_PC_DEVICE_IDS", "robot-allowed")
	for _, token := range []string{"", "short", pcTestToken, "valid-looking-but-contains-spaces 123456"} {
		t.Setenv("STACKCHAN_PC_GATEWAY_TOKEN", token)
		if pcRelayFromEnv().enabled {
			t.Fatalf("unsafe gateway-token configuration enabled relay")
		}
	}
	t.Setenv("STACKCHAN_PC_GATEWAY_TOKEN", pcTestGatewayToken)
	if !pcRelayFromEnv().enabled {
		t.Fatal("valid independent gateway token did not enable relay")
	}
}
