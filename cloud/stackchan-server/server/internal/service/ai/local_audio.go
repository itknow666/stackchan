package ai

import (
	"context"
	"sync"
	"time"

	"github.com/google/uuid"
)

type localAudioOutcome int

const (
	LocalAudioUnavailable localAudioOutcome = iota // definitely no sample started
	LocalAudioCompleted
	LocalAudioInterrupted // accepted, or delivery/result uncertain: never replay
)

func validLocalAudioID(id string) bool {
	if len(id) == 0 || len(id) > 64 {
		return false
	}
	for _, c := range id {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-') {
			return false
		}
	}
	return true
}

// One bounded request per device connection. Incoming acknowledgements are
// handled by the websocket read loop while the provider waits independently.
type localAudioLink struct {
	mu            sync.Mutex
	pack          string
	clips         map[string]bool
	hello, closed bool
	pending       *localAudioPending
	timeout       time.Duration
}
type localAudioPending struct {
	id       string
	accepted bool
	result   chan localAudioOutcome
}

func (l *localAudioLink) negotiate(raw any) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.hello {
		return
	}
	l.hello = true
	c, ok := raw.(map[string]any)
	if !ok || len(c) != 3 || c["version"] != float64(1) {
		return
	}
	pack, _ := c["pack_id"].(string)
	ids, ok := c["clips"].([]any)
	if !validLocalAudioID(pack) || !ok || len(ids) == 0 || len(ids) > 64 {
		return
	}
	clips := map[string]bool{}
	for _, rawID := range ids {
		id, ok := rawID.(string)
		if !ok || !validLocalAudioID(id) || clips[id] {
			return
		}
		clips[id] = true
	}
	l.pack, l.clips = pack, clips
}

func (l *localAudioLink) acknowledge(msg map[string]any) {
	id, _ := msg["request_id"].(string)
	state, _ := msg["state"].(string)
	l.mu.Lock()
	defer l.mu.Unlock()
	p := l.pending
	if p == nil || p.id != id || l.closed {
		return
	}
	switch state {
	case "accepted":
		p.accepted = true
		return
	case "unavailable":
		if p.accepted {
			return
		}
		p.result <- LocalAudioUnavailable
	case "completed":
		if !p.accepted {
			return
		}
		p.result <- LocalAudioCompleted
	case "failed", "cancelled":
		p.result <- LocalAudioInterrupted
	default:
		return
	}
	l.pending = nil // duplicates/stale results cannot complete a new request
}

func (l *localAudioLink) close() {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.closed = true
	if l.pending != nil {
		l.pending.result <- LocalAudioInterrupted
		l.pending = nil
	}
}

// These owned callbacks must return when ctx is cancelled or Close closes the
// socket. call joins its operation before returning: no orphan writer survives
// a timeout. Ordinary cancellation while waiting for a device ACK does not close
// the connection; it first sends a bounded cancellation for the old request.
type localAudioTransport struct {
	Start func(context.Context) error
	Send  func(context.Context, any) error
	Close func()
}

func (t localAudioTransport) call(ctx context.Context, operation func(context.Context) error) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	done := make(chan error, 1)
	go func() { done <- operation(ctx) }()
	finish := func(err error) error {
		if ctx.Err() == context.DeadlineExceeded {
			t.Close()
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return err
	}
	select {
	case err := <-done:
		return finish(err)
	case <-ctx.Done():
	}
	// Context-aware mutex acquisition can finish immediately. Give that path a
	// tiny bounded grace before closing an otherwise healthy barge-in socket.
	grace := time.NewTimer(5 * time.Millisecond)
	defer grace.Stop()
	select {
	case err := <-done:
		return finish(err)
	case <-grace.C:
		t.Close() // closes without the write mutex, interrupting a stuck socket write
		<-done    // join the writer after Close/context has unblocked it
		return ctx.Err()
	}
}

func (l *localAudioLink) play(ctx context.Context, pack, clip string, transport localAudioTransport) localAudioOutcome {
	timeout := l.timeout
	if timeout <= 0 {
		timeout = 20 * time.Second
	}
	// Start the real deadline at entry, before tts:start or request transmission.
	playCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	l.mu.Lock()
	if l.closed || !validLocalAudioID(pack) || !validLocalAudioID(clip) || l.pack != pack || !l.clips[clip] {
		l.mu.Unlock()
		return LocalAudioUnavailable
	}
	if l.pending != nil {
		l.mu.Unlock()
		return LocalAudioInterrupted
	}
	p := &localAudioPending{id: uuid.NewString(), result: make(chan localAudioOutcome, 1)}
	l.pending = p
	l.mu.Unlock()
	defer func() {
		l.mu.Lock()
		if l.pending == p {
			l.pending = nil
		}
		l.mu.Unlock()
	}()
	if playCtx.Err() != nil || transport.Start == nil || transport.Send == nil || transport.Close == nil {
		return LocalAudioInterrupted
	}
	cancelRequest := func() {
		cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), min(timeout, 250*time.Millisecond))
		defer cleanupCancel()
		_ = transport.call(cleanupCtx, func(callCtx context.Context) error {
			return transport.Send(callCtx, map[string]any{"type": "local_audio_cancel", "request_id": p.id})
		})
	}
	// tts:start must precede the local request on the same socket, so firmware
	// schedules Speaking/ResetDecoder before acquiring its local audio lease.
	if transport.call(playCtx, transport.Start) != nil {
		return LocalAudioInterrupted
	}
	if transport.call(playCtx, func(callCtx context.Context) error {
		return transport.Send(callCtx, map[string]any{"type": "local_audio", "request_id": p.id, "pack_id": pack, "clip_id": clip, "timeout_ms": 20000})
	}) != nil {
		// A failed or cancelled send may still have delivered the request.
		// Cancel that exact ID rather than leave an uncertain local clip running.
		cancelRequest()
		return LocalAudioInterrupted
	}
	select {
	case result := <-p.result:
		return result
	case <-playCtx.Done():
	}
	// Result loss can mean the device has already played some/all of the clip.
	// Cancel it and end this response; replaying the full PCM would duplicate it.
	// An independent short grace lets ordinary barge-in cancel the old clip
	// without dropping this connection/new recording. A blocked cancel closes
	// only this socket and joins its transport operation before returning.
	cancelRequest()
	return LocalAudioInterrupted
}
