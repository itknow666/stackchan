/*
SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
SPDX-License-Identifier: MIT
*/

package ai

// The OpenAI-compatible pipeline uploads audio once the turn ends. Most
// compatible endpoints expose HTTP/SSE chat APIs rather than the bidirectional
// Realtime WebSocket protocol, so pretending otherwise would make interruption
// and latency behaviour unreliable. End of turn is whichever comes first: the
// device's listen:stop, or the protocol layer's own silence detection — see
// server_vad.go, without which firmware in auto mode never completes a turn.

import (
	"context"
	"fmt"
	"os"
	"strings"
	"sync"

	"github.com/gogf/gf/v2/frame/g"
	"github.com/gogf/gf/v2/os/gctx"
)

type compatibleConfig struct {
	PC                                     *pcControl
	STTBaseURL, STTAPIKey, STTModel        string
	LLMBaseURL, LLMAPIKey, LLMModel        string
	TTSBaseURL, TTSAPIKey, TTSModel, Voice string
	Prompt                                 string
}

type compatibleSession struct {
	sttClient  *openAIClient
	llmClient  *openAIClient
	ttsClient  *openAIClient
	ha         *haWSClient
	cb         RealtimeCallbacks
	ctx        context.Context
	cancel     context.CancelFunc
	mu         sync.Mutex
	pcm        []int16
	history    []chatMessage
	turnCancel context.CancelFunc
	closed     bool
}

func dialCompatibleSession(ctx context.Context, cfg compatibleConfig, ha *haWSClient, cb RealtimeCallbacks) (RealtimeSession, error) {
	if cfg.STTBaseURL == "" || cfg.STTAPIKey == "" || cfg.STTModel == "" {
		return nil, fmt.Errorf("STT base URL, API key, and model are required")
	}
	if cfg.LLMBaseURL == "" || cfg.LLMAPIKey == "" || cfg.LLMModel == "" {
		return nil, fmt.Errorf("LLM base URL, API key, and model are required")
	}
	if cfg.TTSBaseURL == "" || cfg.TTSAPIKey == "" || cfg.TTSModel == "" {
		return nil, fmt.Errorf("TTS base URL, API key, and model are required")
	}
	childCtx, cancel := context.WithCancel(ctx)
	s := &compatibleSession{
		sttClient: newOpenAIClient(cfg.STTBaseURL, cfg.STTAPIKey, "", cfg.STTModel, "", "", ""),
		llmClient: newOpenAIClient(cfg.LLMBaseURL, cfg.LLMAPIKey, cfg.LLMModel, "", "", "", cfg.Prompt),
		ttsClient: newOpenAIClient(cfg.TTSBaseURL, cfg.TTSAPIKey, "", "", cfg.TTSModel, cfg.Voice, ""),
		ha:        ha, cb: cb, ctx: childCtx, cancel: cancel,
	}
	s.llmClient.pc = cfg.PC
	if os.Getenv("STACKCHAN_ROBOT_TOOLS") == "1" && cb.OnEmotion != nil && cb.OnPose != nil {
		s.llmClient.robot = &robotControl{emotion: cb.OnEmotion, pose: cb.OnPose}
	}
	return s, nil
}

// NeedsServerVAD reports that this provider has no VAD of its own, so the
// protocol layer must decide when the user stopped speaking.
func (s *compatibleSession) NeedsServerVAD() bool { return true }

func (s *compatibleSession) AppendAudio(pcm []int16) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return context.Canceled
	}
	s.pcm = append(s.pcm, pcm...)
	return nil
}

func (s *compatibleSession) CommitAudio() error {
	s.mu.Lock()
	if s.closed || len(s.pcm) == 0 {
		s.mu.Unlock()
		return nil
	}
	pcm := append([]int16(nil), s.pcm...)
	s.pcm = s.pcm[:0]
	if s.turnCancel != nil {
		s.turnCancel()
	}
	turnCtx, turnCancel := context.WithCancel(s.ctx)
	s.turnCancel = turnCancel
	s.mu.Unlock()
	go s.completeTurn(turnCtx, pcm)
	return nil
}

func (s *compatibleSession) completeTurn(ctx context.Context, pcm []int16) {
	// Every turn must report an end, including the ones that produce no audio.
	// Otherwise the protocol layer keeps waiting for a playback completion that
	// never arrives and the device stays deaf for the rest of the session.
	spoke := false
	defer func() {
		if !spoke && ctx.Err() == nil && s.cb.OnStop != nil {
			s.cb.OnStop()
		}
	}()

	text, err := s.sttClient.Transcribe(ctx, pcmToWAV(pcm, 16000))
	if err != nil {
		g.Log().Warningf(gctx.New(), "[COMPAT] transcription: %v", err)
		return
	}
	if text == "" {
		return
	}
	if s.cb.OnSTT != nil {
		s.cb.OnSTT(text)
	}

	s.mu.Lock()
	history := append([]chatMessage(nil), s.history...)
	history = append(history, chatMessage{Role: "user", Content: text})
	s.mu.Unlock()
	clip, recorded, clipErr := recordedVoiceForText(os.Getenv("STACKCHAN_VOICE_PACK_DIR"), text)
	if clipErr != nil {
		g.Log().Warningf(gctx.New(), "[VOICE_PACK] falling back to normal conversation: %v", clipErr)
	}
	reply := clip.Reply
	if recorded && s.llmClient.robot != nil {
		emotion := "happy"
		if strings.Contains(text, "难过") || strings.Contains(text, "伤心") || strings.Contains(text, "心情不好") {
			emotion = "sad"
		}
		if strings.Contains(text, "晚安") || strings.Contains(text, "睡觉") {
			emotion = "sleepy"
		}
		_ = s.cb.OnEmotion(emotion)
	}
	if !recorded {
		reply, err = s.llmClient.Chat(ctx, history, s.ha)
		if err != nil {
			g.Log().Warningf(gctx.New(), "[COMPAT] chat: %v", err)
			return
		}
	}
	if reply == "" || ctx.Err() != nil {
		return
	}
	if s.cb.OnText != nil {
		s.cb.OnText(reply)
	}
	pcmReply := clip.PCM
	if !recorded {
		pcmReply, err = s.ttsClient.Speak(ctx, reply)
		if err != nil {
			g.Log().Warningf(gctx.New(), "[COMPAT] TTS: %v", err)
			return
		}
	}
	if ctx.Err() != nil {
		return
	}
	// The speaking phase reports its own end through OnStop below.
	spoke = true
	if s.cb.OnStart != nil {
		s.cb.OnStart()
	}
	local := LocalAudioUnavailable
	if recorded && clip.PackID != "" && s.cb.OnLocalClip != nil {
		local = s.cb.OnLocalClip(ctx, clip.PackID, clip.ID)
	}
	if ctx.Err() != nil {
		// The abort/disconnect owner ends playback; never let a cancelled old
		// local request stop or replay audio into a later turn.
		return
	}
	if local == LocalAudioUnavailable && len(pcmReply) > 0 {
		if s.cb.OnAudioContext != nil {
			if err = s.cb.OnAudioContext(ctx, pcmReply); err != nil && ctx.Err() == nil {
				g.Log().Warningf(gctx.New(), "[COMPAT] playback: %v", err)
			}
		} else if s.cb.OnAudio != nil {
			s.cb.OnAudio(pcmReply)
		}
	}
	if ctx.Err() != nil {
		return
	}
	if local == LocalAudioInterrupted {
		g.Log().Warning(gctx.New(), "[COMPAT] local recording interrupted; full recording will not be replayed")
	}
	if s.cb.OnStop != nil {
		s.cb.OnStop()
	}

	s.mu.Lock()
	s.history = append(history, chatMessage{Role: "assistant", Content: reply})
	s.mu.Unlock()
}

func (s *compatibleSession) CancelResponse() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.turnCancel != nil {
		s.turnCancel()
		s.turnCancel = nil
	}
	return nil
}

func (s *compatibleSession) Close() {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return
	}
	s.closed = true
	if s.turnCancel != nil {
		s.turnCancel()
	}
	s.mu.Unlock()
	s.cancel()
}
