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

func (l *localAudioLink) play(ctx context.Context, pack, clip string, start func(), send func(any) error) localAudioOutcome {
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
	timeout := l.timeout
	if timeout <= 0 {
		timeout = 20 * time.Second
	}
	l.mu.Unlock()
	defer func() {
		l.mu.Lock()
		if l.pending == p {
			l.pending = nil
		}
		l.mu.Unlock()
	}()
	if ctx.Err() != nil {
		return LocalAudioInterrupted
	}
	// tts:start must precede the local request on the same socket, so firmware
	// schedules Speaking/ResetDecoder before acquiring its local audio lease.
	start()
	if send(map[string]any{"type": "local_audio", "request_id": p.id, "pack_id": pack, "clip_id": clip, "timeout_ms": 20000}) != nil {
		return LocalAudioInterrupted
	}
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	select {
	case result := <-p.result:
		return result
	case <-ctx.Done():
	case <-timer.C:
	}
	// Result loss can mean the device has already played some/all of the clip.
	// Cancel it and end this response; replaying the full PCM would duplicate it.
	_ = send(map[string]any{"type": "local_audio_cancel", "request_id": p.id})
	return LocalAudioInterrupted
}
