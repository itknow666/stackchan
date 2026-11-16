package ai

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func TestLocalAudioWebSocketHandshakeAndPhysicalCompletion(t *testing.T) {
	result := make(chan localAudioOutcome, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close()
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		var hello map[string]any
		if conn.ReadJSON(&hello) != nil {
			return
		}
		link := localAudioLink{}
		link.negotiate(hello["local_audio"])
		done := make(chan localAudioOutcome, 1)
		go func() {
			done <- link.play(ctx, "pack", "clip", func() { _ = conn.WriteJSON(map[string]any{"type": "tts", "state": "start"}) }, conn.WriteJSON)
		}()
		for {
			var ack map[string]any
			if conn.ReadJSON(&ack) != nil {
				link.close()
				return
			}
			link.acknowledge(ack)
			if ack["state"] == "completed" {
				select {
				case outcome := <-done:
					result <- outcome
				case <-ctx.Done():
				}
				return
			}
		}
	}))
	defer server.Close()
	conn, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(server.URL, "http"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	_ = conn.SetReadDeadline(time.Now().Add(time.Second))
	if err = conn.WriteJSON(map[string]any{"type": "hello", "local_audio": localHello("pack", "clip")}); err != nil {
		t.Fatal(err)
	}
	var message map[string]any
	if err = conn.ReadJSON(&message); err != nil || message["type"] != "tts" || message["state"] != "start" {
		t.Fatalf("start ordering %v %v", message, err)
	}
	if err = conn.ReadJSON(&message); err != nil || message["type"] != "local_audio" || message["clip_id"] != "clip" {
		t.Fatalf("local request %v %v", message, err)
	}
	id := message["request_id"]
	if err = conn.WriteJSON(map[string]any{"type": "local_audio", "request_id": id, "state": "accepted"}); err != nil {
		t.Fatal(err)
	}
	select {
	case <-result:
		t.Fatal("acceptance was mistaken for completion")
	case <-time.After(10 * time.Millisecond):
	}
	if err = conn.WriteJSON(map[string]any{"type": "local_audio", "request_id": id, "state": "completed"}); err != nil {
		t.Fatal(err)
	}
	select {
	case outcome := <-result:
		if outcome != LocalAudioCompleted {
			t.Fatal(outcome)
		}
	case <-time.After(time.Second):
		t.Fatal("completion not observed")
	}
}

func localHello(pack string, ids ...string) any {
	list := make([]any, len(ids))
	for i, id := range ids {
		list[i] = id
	}
	return map[string]any{"version": float64(1), "pack_id": pack, "clips": list}
}
func localAck(l *localAudioLink, id, state string) {
	l.acknowledge(map[string]any{"request_id": id, "state": state})
}

func TestLocalAudioNegotiationRequiresExactSafePack(t *testing.T) {
	for _, raw := range []any{nil, localHello("wrong", "clip"), localHello("pack", "other"), localHello("pack", "../clip"), localHello("pack", "clip", "clip"), localHello("../pack", "clip"), localHello("pack")} {
		l := localAudioLink{}
		l.negotiate(raw)
		got := l.play(context.Background(), "pack", "clip", func() { t.Fatal("started incompatible local audio") }, func(any) error { t.Fatal("sent incompatible local audio"); return nil })
		if got != LocalAudioUnavailable {
			t.Fatalf("%v: %v", raw, got)
		}
	}
	l := localAudioLink{}
	l.negotiate(nil)
	l.negotiate(localHello("pack", "clip"))
	if l.pack != "" {
		t.Fatal("duplicate hello changed capabilities")
	}
}

func TestLocalAudioAcknowledgementsAndNoReplay(t *testing.T) {
	for _, scenario := range []struct {
		states []string
		want   localAudioOutcome
	}{
		{[]string{"accepted", "completed"}, LocalAudioCompleted},
		{[]string{"unavailable"}, LocalAudioUnavailable},
		{[]string{"failed"}, LocalAudioInterrupted},
		{[]string{"accepted", "unavailable", "failed"}, LocalAudioInterrupted},
		{[]string{"accepted", "cancelled"}, LocalAudioInterrupted},
	} {
		l := localAudioLink{}
		l.negotiate(localHello("pack", "clip"))
		started := false
		calls := 0
		got := l.play(context.Background(), "pack", "clip", func() { started = true }, func(raw any) error {
			if !started {
				t.Fatal("local request preceded tts:start")
			}
			calls++
			msg := raw.(map[string]any)
			id := msg["request_id"].(string)
			localAck(&l, "stale", "completed")
			localAck(&l, id, "completed") // completion before acceptance ignored
			for _, state := range scenario.states {
				localAck(&l, id, state)
			}
			localAck(&l, id, "completed") // duplicate terminal ignored
			return nil
		})
		if got != scenario.want || calls != 1 {
			t.Fatalf("%v = %v calls=%d", scenario.states, got, calls)
		}
	}
}

func TestLocalAudioTimeoutCancellationDisconnectAndSendFailure(t *testing.T) {
	for _, mode := range []string{"timeout", "cancel", "disconnect", "send_failure"} {
		t.Run(mode, func(t *testing.T) {
			l := localAudioLink{timeout: 10 * time.Millisecond}
			l.negotiate(localHello("pack", "clip"))
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			cancelMessages := 0
			got := l.play(ctx, "pack", "clip", func() {}, func(raw any) error {
				msg := raw.(map[string]any)
				if msg["type"] == "local_audio_cancel" {
					cancelMessages++
					return nil
				}
				if mode == "cancel" {
					cancel()
				}
				if mode == "disconnect" {
					l.close()
				}
				if mode == "send_failure" {
					return errors.New("socket closed")
				}
				return nil
			})
			if got != LocalAudioInterrupted {
				t.Fatalf("uncertain outcome permitted replay: %v", got)
			}
			if (mode == "timeout" || mode == "cancel") && cancelMessages != 1 {
				t.Fatal("missing cancel")
			}
			if l.pending != nil {
				t.Fatal("pending request leaked")
			}
		})
	}
}

func TestLocalAudioStaleResultCannotEndNewRequest(t *testing.T) {
	l := localAudioLink{}
	l.negotiate(localHello("pack", "clip"))
	old := ""
	for n := 0; n < 2; n++ {
		got := l.play(context.Background(), "pack", "clip", func() {}, func(raw any) error {
			id := raw.(map[string]any)["request_id"].(string)
			if id == old {
				t.Fatal("request ID reused")
			}
			localAck(&l, old, "accepted")
			localAck(&l, old, "completed")
			localAck(&l, id, "accepted")
			localAck(&l, id, "completed")
			old = id
			return nil
		})
		if got != LocalAudioCompleted {
			t.Fatal(got)
		}
	}
}

func TestLocalAudioConcurrentRequestBound(t *testing.T) {
	l := localAudioLink{}
	l.negotiate(localHello("pack", "clip"))
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	sent := make(chan struct{})
	done := make(chan localAudioOutcome)
	go func() {
		done <- l.play(ctx, "pack", "clip", func() {}, func(raw any) error {
			if raw.(map[string]any)["type"] == "local_audio" {
				close(sent)
			}
			return nil
		})
	}()
	<-sent
	if l.play(ctx, "pack", "clip", func() { t.Fatal("second start") }, func(any) error { t.Fatal("second send"); return nil }) != LocalAudioInterrupted {
		t.Fatal("concurrent request admitted")
	}
	cancel()
	if <-done != LocalAudioInterrupted {
		t.Fatal("cancel result")
	}
}

func voicePackWithLocalMetadata(t *testing.T) string {
	dir := testVoicePack(t)
	path := filepath.Join(dir, "manifest.json")
	raw, _ := os.ReadFile(path)
	var pack recordedVoicePack
	json.Unmarshal(raw, &pack)
	pack.PackID = "pack"
	pack.Rules[0].ID = "welcome"
	raw, _ = json.Marshal(pack)
	os.WriteFile(path, raw, 0600)
	return dir
}

func TestLocalAudioCompatibleFallbackAndCompletion(t *testing.T) {
	t.Setenv("STACKCHAN_VOICE_PACK_DIR", voicePackWithLocalMetadata(t))
	for _, outcome := range []localAudioOutcome{LocalAudioUnavailable, LocalAudioCompleted, LocalAudioInterrupted} {
		events := []string{}
		cb := RealtimeCallbacks{OnStart: func() { events = append(events, "start") }, OnLocalClip: func(ctx context.Context, pack, id string) localAudioOutcome {
			if pack != "pack" || id != "welcome" {
				t.Fatal("metadata lost")
			}
			events = append(events, "local")
			return outcome
		}, OnAudio: func([]int16) { events = append(events, "pcm") }, OnStop: func() { events = append(events, "stop") }}
		s := compatibleSessionForTest(t, cb, func(path string) (*http.Response, error) {
			if strings.HasSuffix(path, "/audio/transcriptions") {
				return jsonResponse(`{"text":"我回来了"}`), nil
			}
			return nil, fmt.Errorf("unexpected cloud call")
		})
		s.completeTurn(context.Background(), make([]int16, deviceFrameSamples))
		want := "start,local,stop"
		if outcome == LocalAudioUnavailable {
			want = "start,local,pcm,stop"
		}
		if strings.Join(events, ",") != want {
			t.Fatalf("outcome=%v events=%v", outcome, events)
		}
	}
}

func TestLocalAudioCancelledCompatibleDoesNotStopNewTurn(t *testing.T) {
	t.Setenv("STACKCHAN_VOICE_PACK_DIR", voicePackWithLocalMetadata(t))
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s := compatibleSessionForTest(t, RealtimeCallbacks{OnLocalClip: func(context.Context, string, string) localAudioOutcome { cancel(); return LocalAudioInterrupted },
		OnStop: func() { t.Error("cancelled old callback stopped playback") }, OnAudio: func([]int16) { t.Error("cancelled turn replayed PCM") }},
		func(path string) (*http.Response, error) { return jsonResponse(`{"text":"我回来了"}`), nil })
	s.completeTurn(ctx, make([]int16, deviceFrameSamples))
}
