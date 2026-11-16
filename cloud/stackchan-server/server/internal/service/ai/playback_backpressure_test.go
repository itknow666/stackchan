package ai

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"
)

func audioQueueSession(t *testing.T) *wsSession {
	t.Helper()
	enc, err := newOpusStreamEncoder()
	if err != nil {
		t.Fatal(err)
	}
	return &wsSession{activity: newConversationActivity(0, time.Now()), opusEnc: enc,
		frameQueue: make(chan []byte, 1), playbackStarted: true, firstAudioLogged: 1}
}

func TestLongAudioWaitsForCapacityWithoutDropping(t *testing.T) {
	s := audioQueueSession(t)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	count := make(chan int, 1)
	go func() {
		frames := 0
		for range s.frameQueue {
			frames++
		}
		count <- frames
	}()
	// More than the production queue's 36 seconds; the tiny test queue must
	// exert backpressure instead of allowing any part of the story to vanish.
	pcm := make([]int16, 61*serverSampleRate)
	err := s.enqueueAudio(ctx, pcm)
	close(s.frameQueue)
	if err != nil {
		t.Fatal(err)
	}
	if got, want := <-count, len(pcm)/(serverSampleRate*frameDurationMs/1000); got != want {
		t.Fatalf("delivered %d frames, want %d", got, want)
	}
}

func TestBlockedAudioDeliveryCanBeCancelled(t *testing.T) {
	s := audioQueueSession(t)
	s.frameQueue <- []byte{1}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- s.enqueueAudio(ctx, make([]int16, serverSampleRate)) }()
	select {
	case err := <-done:
		t.Fatalf("full queue did not block: %v", err)
	case <-time.After(20 * time.Millisecond):
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("cancel: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("cancel left delivery blocked")
	}
}

func TestSpeakingStartsAfterSynthesisIsReady(t *testing.T) {
	started, audio := false, false
	s := compatibleSessionForTest(t, RealtimeCallbacks{
		OnStart: func() { started = true },
		OnAudio: func([]int16) {
			if !started {
				t.Error("audio before start")
			}
			audio = true
		},
	}, func(path string) (*http.Response, error) {
		switch {
		case strings.HasSuffix(path, "/audio/transcriptions"):
			return jsonResponse("{\"text\":\"讲个故事\"}"), nil
		case strings.HasSuffix(path, "/chat/completions"):
			return jsonResponse("{\"choices\":[{\"message\":{\"content\":\"从前有座山\"}}]}"), nil
		case strings.HasSuffix(path, "/audio/speech"):
			if started {
				t.Error("speaking state sent while synthesis was still pending")
			}
			return pcmResponse(480), nil
		}
		t.Fatalf("unexpected route %s", path)
		return nil, nil
	})
	s.completeTurn(s.ctx, make([]int16, deviceFrameSamples))
	if !started || !audio {
		t.Fatal("missing completed speech")
	}
}

func TestCancelledSynthesisDoesNotEmitStalePlaybackEvents(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	events := 0
	s := compatibleSessionForTest(t, RealtimeCallbacks{
		OnStart: func() { events++ }, OnStop: func() { events++ }, OnAudio: func([]int16) { events++ },
	}, func(path string) (*http.Response, error) {
		switch {
		case strings.HasSuffix(path, "/audio/transcriptions"):
			return jsonResponse("{\"text\":\"讲个故事\"}"), nil
		case strings.HasSuffix(path, "/chat/completions"):
			return jsonResponse("{\"choices\":[{\"message\":{\"content\":\"从前有座山\"}}]}"), nil
		case strings.HasSuffix(path, "/audio/speech"):
			cancel()
			return pcmResponse(480), nil
		}
		t.Fatalf("unexpected route %s", path)
		return nil, nil
	})
	s.completeTurn(ctx, make([]int16, deviceFrameSamples))
	if events != 0 {
		t.Fatalf("cancelled old turn emitted %d playback events", events)
	}
}
