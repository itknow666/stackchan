package ai

import (
	"context"
	"errors"
	"sync"
	"time"
)

const deviceWriteTimeout = 5 * time.Second

// The data writer remains serialized, but waiting for its mutex is cancellable.
// In particular an old PCM write cannot strand a new local-audio request.
func lockDeviceMutex(ctx context.Context, mu *sync.Mutex) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if mu.TryLock() {
		return nil
	}
	ticker := time.NewTicker(time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			if mu.TryLock() {
				if err := ctx.Err(); err != nil {
					mu.Unlock()
					return err
				}
				return nil
			}
		}
	}
}

func (s *wsSession) closeTransport() {
	// gorilla Close does not require writeMu and interrupts existing writes.
	if s.conn != nil {
		_ = s.conn.Close()
	}
}

func (s *wsSession) writeDeviceMessage(parent context.Context, kind int, data []byte) error {
	ctx, cancel := context.WithTimeout(parent, deviceWriteTimeout)
	defer cancel()
	if err := lockDeviceMutex(ctx, &s.writeMu); err != nil {
		if ctx.Err() == context.DeadlineExceeded {
			s.closeTransport()
		}
		return err
	}
	defer s.writeMu.Unlock()
	if s.conn == nil {
		return errors.New("device websocket unavailable")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	deadline, _ := ctx.Deadline()
	if err := s.conn.SetWriteDeadline(deadline); err != nil {
		s.closeTransport()
		return err
	}
	if err := s.conn.WriteMessage(kind, data); err != nil {
		s.closeTransport()
		return err
	}
	return nil
}
