package ai

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func TestLocalAudioBlockedTransportDeadlineClosesAndJoins(t *testing.T) {
	for _, phase := range []string{"start", "request", "cancel"} {
		t.Run(phase, func(t *testing.T) {
			link := localAudioLink{timeout: 10 * time.Millisecond}
			link.negotiate(localHello("pack", "clip"))
			unblock := make(chan struct{})
			var once sync.Once
			var active atomic.Int32
			var closed atomic.Bool
			block := func() { active.Add(1); defer active.Add(-1); <-unblock }
			transport := localAudioTransport{
				Start: func(context.Context) error {
					if phase == "start" {
						block()
					}
					return nil
				},
				Send: func(_ context.Context, raw any) error {
					message := raw.(map[string]any)
					if phase == "request" && message["type"] == "local_audio" || phase == "cancel" && message["type"] == "local_audio_cancel" {
						block()
					}
					if closed.Load() {
						return net.ErrClosed
					}
					return nil
				},
				Close: func() { closed.Store(true); once.Do(func() { close(unblock) }) },
			}
			done := make(chan localAudioOutcome, 1)
			go func() { done <- link.play(context.Background(), "pack", "clip", transport) }()
			select {
			case outcome := <-done:
				if outcome != LocalAudioInterrupted {
					t.Fatal(outcome)
				}
			case <-time.After(200 * time.Millisecond):
				transport.Close()
				<-done
				t.Fatal("transport phase exceeded its bounded deadline")
			}
			if !closed.Load() || active.Load() != 0 {
				t.Fatalf("close=%v active orphan operations=%d", closed.Load(), active.Load())
			}
			link.mu.Lock()
			pending := link.pending
			link.mu.Unlock()
			if pending != nil {
				t.Fatal("pending request leaked")
			}
		})
	}
}

// This gate wraps a real upgraded websocket connection. Its data write stays
// blocked even when a write deadline is set, and Close must interrupt it.
type localAudioGateConn struct {
	net.Conn
	block                atomic.Bool
	started, closed      chan struct{}
	startOnce, closeOnce sync.Once
}

func (c *localAudioGateConn) Write(data []byte) (int, error) {
	if c.block.Load() {
		c.startOnce.Do(func() { close(c.started) })
		<-c.closed
		return 0, net.ErrClosed
	}
	return c.Conn.Write(data)
}
func (c *localAudioGateConn) Close() error {
	c.closeOnce.Do(func() { close(c.closed) })
	return c.Conn.Close()
}

type localAudioGateListener struct {
	net.Listener
	connections chan *localAudioGateConn
}

func (l *localAudioGateListener) Accept() (net.Conn, error) {
	conn, err := l.Listener.Accept()
	if err != nil {
		return nil, err
	}
	gate := &localAudioGateConn{Conn: conn, started: make(chan struct{}), closed: make(chan struct{})}
	l.connections <- gate
	return gate, nil
}

func localAudioSocketPair(t *testing.T) (*wsSession, *websocket.Conn, *localAudioGateConn) {
	t.Helper()
	sessions := make(chan *wsSession, 1)
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close()
		s := &wsSession{conn: conn}
		s.localAudio.negotiate(localHello("pack", "clip"))
		defer s.localAudio.close()
		sessions <- s
		for {
			var ack map[string]any
			if conn.ReadJSON(&ack) != nil {
				return
			}
			s.localAudio.acknowledge(ack)
		}
	}))
	listener := &localAudioGateListener{Listener: server.Listener, connections: make(chan *localAudioGateConn, 1)}
	server.Listener = listener
	server.Start()
	t.Cleanup(server.Close)
	peer, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(server.URL, "http"), nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { peer.Close() })
	return <-sessions, peer, <-listener.connections
}

func TestLocalAudioWriteMutexBackpressureDeadlineAndCancellation(t *testing.T) {
	for _, mode := range []string{"deadline", "parent_cancel"} {
		t.Run(mode, func(t *testing.T) {
			s, _, gate := localAudioSocketPair(t)
			gate.block.Store(true)
			oldWriter := make(chan error, 1)
			go func() { oldWriter <- s.sendAudio([]byte{1, 2}) }()
			select {
			case <-gate.started:
			case <-time.After(time.Second):
				t.Fatal("old audio write did not block")
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			s.localAudio.timeout = 20 * time.Millisecond
			if mode == "parent_cancel" {
				s.localAudio.timeout = 60 * time.Millisecond
			}
			started := make(chan struct{})
			var once sync.Once
			var active atomic.Int32
			transport := localAudioTransport{Start: func(context.Context) error { return nil }, Close: s.closeTransport,
				Send: func(callCtx context.Context, raw any) error {
					active.Add(1)
					defer active.Add(-1)
					once.Do(func() { close(started) })
					return s.sendJSONContext(callCtx, raw)
				}}
			done := make(chan localAudioOutcome, 1)
			go func() { done <- s.localAudio.play(ctx, "pack", "clip", transport) }()
			if mode == "parent_cancel" {
				<-started
				cancel()
			}
			select {
			case result := <-done:
				if result != LocalAudioInterrupted {
					t.Fatal(result)
				}
			case <-time.After(250 * time.Millisecond):
				s.closeTransport()
				<-done
				t.Fatal("local request blocked behind old audio writer")
			}
			select {
			case err := <-oldWriter:
				if err == nil {
					t.Fatal("blocked old write unexpectedly succeeded")
				}
			case <-time.After(time.Second):
				t.Fatal("old socket writer was not joined after close")
			}
			if active.Load() != 0 {
				t.Fatal("local transport writer survived return")
			}
			if !s.writeMu.TryLock() {
				t.Fatal("write mutex still owned after all writers ended")
			}
			s.writeMu.Unlock()
		})
	}
}

func TestLocalAudioHealthyBargeInRetainsSocketAndNextTurn(t *testing.T) {
	s, peer, gate := localAudioSocketPair(t)
	s.localAudio.timeout = time.Second
	transport := localAudioTransport{Start: func(context.Context) error { return nil }, Send: s.sendJSONContext, Close: s.closeTransport}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan localAudioOutcome, 1)
	go func() { done <- s.localAudio.play(ctx, "pack", "clip", transport) }()
	_ = peer.SetReadDeadline(time.Now().Add(time.Second))
	var first map[string]any
	if peer.ReadJSON(&first) != nil || first["type"] != "local_audio" {
		t.Fatal("missing first local request")
	}
	if err := peer.WriteJSON(map[string]any{"type": "local_audio", "request_id": first["request_id"], "state": "accepted"}); err != nil {
		t.Fatal(err)
	}
	cancel()
	var message map[string]any
	if err := peer.ReadJSON(&message); err != nil || message["type"] != "local_audio_cancel" || message["request_id"] != first["request_id"] {
		t.Fatalf("cancel=%v err=%v", message, err)
	}
	if <-done != LocalAudioInterrupted {
		t.Fatal("old turn did not end")
	}
	select {
	case <-gate.closed:
		t.Fatal("healthy barge-in closed the connection")
	default:
	}
	go func() { done <- s.localAudio.play(context.Background(), "pack", "clip", transport) }()
	if err := peer.ReadJSON(&message); err != nil || message["type"] != "local_audio" || message["request_id"] == first["request_id"] {
		t.Fatalf("next request=%v err=%v", message, err)
	}
	for _, state := range []string{"accepted", "completed"} {
		if err := peer.WriteJSON(map[string]any{"type": "local_audio", "request_id": message["request_id"], "state": state}); err != nil {
			t.Fatal(err)
		}
	}
	select {
	case result := <-done:
		if result != LocalAudioCompleted {
			t.Fatal(result)
		}
	case <-time.After(time.Second):
		t.Fatal("next turn failed on retained socket")
	}
}

func TestLocalAudioWriteMutexWithoutOwnerReleaseIsCancellable(t *testing.T) {
	s := &wsSession{}
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- s.sendJSONContext(ctx, map[string]any{"type": "test"}) }()
	select {
	case err := <-done:
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal(err)
		}
	case <-time.After(100 * time.Millisecond):
		t.Fatal("mutex acquisition ignored cancellation")
	}
}
