package ai

// The computer initiates this connection. A robot can only use the bounded
// capabilities of that live connection; nothing is persisted or retried.
import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/gorilla/websocket"
)

const pcMaxFrame = 16 * 1024
const pcMaxPending = 4
const pcGatewayHeader = "X-Stackchan-PC-Device-Token"

var pcIdentifier = regexp.MustCompile(`^[a-zA-Z0-9_-]{1,64}$`)
var pcActions = map[string]bool{"open_app": true, "open_project": true, "set_volume": true, "media": true, "search_web": true, "lock": true, "codex_start": true, "codex_status": true, "codex_cancel": true}

type pcItem struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}
type pcCapabilities struct {
	Apps     []pcItem `json:"apps"`
	Projects []pcItem `json:"projects"`
	Actions  []string `json:"actions"`
}
type pcHello struct {
	Type         string         `json:"type"`
	Version      int            `json:"version"`
	PCID         string         `json:"pc_id"`
	Capabilities pcCapabilities `json:"capabilities"`
}
type pcResult struct {
	Type    string         `json:"type"`
	ID      string         `json:"id"`
	OK      *bool          `json:"ok"`
	Message *string        `json:"message"`
	Data    map[string]any `json:"data"`
}
type pcOutcome struct {
	result pcResult
	err    error
}
type pcPending struct {
	done     chan pcOutcome
	deadline time.Time
}
type pcRelay struct {
	token, gatewayToken, pcID               string
	devices                                 map[string]bool
	enabled                                 bool
	timeout                                 time.Duration
	helloTimeout, pingInterval, pongTimeout time.Duration
	mu                                      sync.Mutex
	peer                                    *pcPeer
}
type pcPeer struct {
	relay         *pcRelay
	conn          *websocket.Conn
	caps          pcCapabilities // immutable after hello
	writeMu       sync.Mutex
	mu            sync.Mutex
	ready, closed bool
	pending       map[string]*pcPending
	done          chan struct{}
	closeOnce     sync.Once
}

func newPCRelay(token, gatewayToken, pcID string, devices []string) *pcRelay {
	if pcID == "" {
		pcID = "desktop"
	}
	r := &pcRelay{token: token, gatewayToken: gatewayToken, pcID: pcID, devices: map[string]bool{}, timeout: 15 * time.Second, helloTimeout: 5 * time.Second, pingInterval: 20 * time.Second, pongTimeout: 45 * time.Second}
	for _, id := range devices {
		if id = strings.TrimSpace(id); id != "" {
			r.devices[id] = true
		}
	}
	r.enabled = len(token) >= 32 && len(token) <= 4096 && strings.IndexFunc(token, unicode.IsSpace) < 0 && validPCGatewayToken(gatewayToken) && token != gatewayToken && pcIdentifier.MatchString(pcID) && len(r.devices) > 0
	return r
}
func pcRelayFromEnv() *pcRelay {
	return newPCRelay(os.Getenv("STACKCHAN_PC_TOKEN"), os.Getenv("STACKCHAN_PC_GATEWAY_TOKEN"), os.Getenv("STACKCHAN_PC_ID"), strings.Split(os.Getenv("STACKCHAN_PC_DEVICE_IDS"), ","))
}

func validPCGatewayToken(token string) bool {
	if len(token) < 32 || len(token) > 4096 {
		return false
	}
	for _, ch := range token {
		if !(ch >= 'a' && ch <= 'z' || ch >= 'A' && ch <= 'Z' || ch >= '0' && ch <= '9' || ch == '_' || ch == '-') {
			return false
		}
	}
	return true
}

type pcGrantKey struct{}
type pcGrant struct {
	relay    *pcRelay
	deviceID string
}

// Device-Id identifies a robot but does not authenticate it. Only the trusted
// secret-path gateway may assert PC access; public robot firmware needs no new
// credential. Never copy this gateway-only token to a device or PC client.
func pcRequestContext(ctx context.Context, relay *pcRelay, req *http.Request) context.Context {
	ctx = context.WithValue(ctx, pcGrantKey{}, (*pcGrant)(nil)) // revoke any inherited request grant
	if relay == nil || !relay.enabled || len(req.Header.Values(pcGatewayHeader)) != 1 {
		return ctx
	}
	want, got := sha256.Sum256([]byte(relay.gatewayToken)), sha256.Sum256([]byte(req.Header.Get(pcGatewayHeader)))
	if subtle.ConstantTimeCompare(want[:], got[:]) != 1 {
		return ctx
	}
	device := req.Header.Get("Device-Id")
	if !relay.authorized(device) {
		return ctx
	}
	return context.WithValue(ctx, pcGrantKey{}, &pcGrant{relay: relay, deviceID: device})
}
func (r *pcRelay) authorizedContext(ctx context.Context, device string) bool {
	grant, _ := ctx.Value(pcGrantKey{}).(*pcGrant)
	return r.authorized(device) && grant != nil && grant.relay == r && grant.deviceID == device
}

var defaultPCRelay = pcRelayFromEnv()

func HandlePCWS(w http.ResponseWriter, req *http.Request) { defaultPCRelay.ServeHTTP(w, req) }
func (r *pcRelay) authorized(device string) bool          { return r != nil && r.enabled && r.devices[device] }

// strictPCJSON rejects duplicate keys as well as unknown fields and trailing data.
// Duplicate keys must never let authentication/validation and execution disagree.
func strictPCJSON(raw []byte, dst any) error {
	if len(raw) == 0 || len(raw) > pcMaxFrame || !utf8.Valid(raw) {
		return errors.New("invalid frame size or encoding")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	var walk func() error
	walk = func() error {
		tok, err := d.Token()
		if err != nil {
			return err
		}
		if delim, ok := tok.(json.Delim); ok {
			switch delim {
			case '{':
				seen := map[string]bool{}
				for d.More() {
					k, err := d.Token()
					if err != nil {
						return err
					}
					key, ok := k.(string)
					if !ok || seen[key] {
						return errors.New("duplicate field")
					}
					seen[key] = true
					if err := walk(); err != nil {
						return err
					}
				}
			case '[':
				for d.More() {
					if err := walk(); err != nil {
						return err
					}
				}
			default:
				return errors.New("invalid delimiter")
			}
			_, err = d.Token()
			return err
		}
		return nil
	}
	if err := walk(); err != nil {
		return err
	}
	if _, err := d.Token(); err != io.EOF {
		return errors.New("trailing data")
	}
	d = json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	return d.Decode(dst)
}

// encoding/json normally matches struct fields case-insensitively. This wire
// protocol uses exact keys, matching the Python client, so check their spelling.
func pcFields(raw []byte, names ...string) (map[string]json.RawMessage, bool) {
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || len(fields) != len(names) {
		return nil, false
	}
	for _, name := range names {
		if _, ok := fields[name]; !ok {
			return nil, false
		}
	}
	return fields, true
}
func decodePCHello(raw []byte, hello *pcHello) bool {
	if strictPCJSON(raw, hello) != nil {
		return false
	}
	fields, ok := pcFields(raw, "type", "version", "pc_id", "capabilities")
	if !ok {
		return false
	}
	caps, ok := pcFields(fields["capabilities"], "apps", "projects", "actions")
	if !ok {
		return false
	}
	for _, name := range []string{"apps", "projects"} {
		var items []json.RawMessage
		if json.Unmarshal(caps[name], &items) != nil {
			return false
		}
		for _, item := range items {
			if _, ok := pcFields(item, "id", "name"); !ok {
				return false
			}
		}
	}
	return true
}
func decodePCResult(raw []byte, result *pcResult) bool {
	if strictPCJSON(raw, result) != nil {
		return false
	}
	_, ok := pcFields(raw, "type", "id", "ok", "message", "data")
	return ok
}

func (r *pcRelay) ServeHTTP(w http.ResponseWriter, req *http.Request) {
	if !r.enabled {
		http.Error(w, "Computer relay disabled", http.StatusServiceUnavailable)
		return
	}
	if req.Method != http.MethodGet {
		http.Error(w, "GET required", http.StatusMethodNotAllowed)
		return
	}
	want, got := sha256.Sum256([]byte("Bearer "+r.token)), sha256.Sum256([]byte(req.Header.Get("Authorization")))
	if subtle.ConstantTimeCompare(want[:], got[:]) != 1 {
		http.Error(w, "Unauthorized", http.StatusUnauthorized)
		return
	}
	upgrade := websocket.Upgrader{HandshakeTimeout: r.helloTimeout, CheckOrigin: func(req *http.Request) bool { return req.Header.Get("Origin") == "" }, EnableCompression: false}
	conn, err := upgrade.Upgrade(w, req, nil)
	if err != nil {
		return
	}
	p := &pcPeer{relay: r, conn: conn, pending: map[string]*pcPending{}, done: make(chan struct{})}
	defer p.close()
	conn.SetReadLimit(pcMaxFrame)
	_ = conn.SetReadDeadline(time.Now().Add(r.helloTimeout))
	kind, raw, err := conn.ReadMessage()
	var hello pcHello
	if err != nil || kind != websocket.TextMessage || !decodePCHello(raw, &hello) || hello.Type != "hello" || hello.Version != 1 || hello.PCID != r.pcID || !validPCCapabilities(hello.Capabilities) {
		return
	}
	p.caps = hello.Capabilities
	r.mu.Lock()
	if r.peer != nil {
		r.mu.Unlock()
		return
	} // never take over an active computer
	r.peer = p
	r.mu.Unlock()
	if p.write(map[string]any{"type": "ready", "version": 1}) != nil {
		return
	}
	p.mu.Lock()
	p.ready = true
	p.mu.Unlock()
	_ = conn.SetReadDeadline(time.Now().Add(r.pongTimeout))
	conn.SetPongHandler(func(string) error { return conn.SetReadDeadline(time.Now().Add(r.pongTimeout)) })
	go p.ping()
	for {
		kind, raw, err = conn.ReadMessage()
		if err != nil {
			return
		}
		var result pcResult
		if kind != websocket.TextMessage || !decodePCResult(raw, &result) || result.Type != "result" || result.ID == "" || len(result.ID) > 128 || result.OK == nil || result.Data == nil || result.Message == nil || len(*result.Message) > 2048 {
			return
		}
		p.mu.Lock()
		pending := p.pending[result.ID]
		if pending == nil {
			p.mu.Unlock()
			return
		} // cross-connection/unsolicited/duplicate result
		delete(p.pending, result.ID)
		if time.Now().After(pending.deadline) {
			pending.done <- pcOutcome{err: errors.New("computer action timed out; execution outcome unknown; not retried")}
		} else {
			pending.done <- pcOutcome{result: result}
		}
		p.mu.Unlock()
	}
}
func validPCCapabilities(c pcCapabilities) bool {
	if c.Apps == nil || c.Projects == nil || c.Actions == nil || len(c.Apps) > 64 || len(c.Projects) > 64 || len(c.Actions) > len(pcActions) {
		return false
	}
	for _, list := range [][]pcItem{c.Apps, c.Projects} {
		seen := map[string]bool{}
		for _, item := range list {
			if !pcIdentifier.MatchString(item.ID) || seen[item.ID] || utf8.RuneCountInString(item.Name) == 0 || utf8.RuneCountInString(item.Name) > 100 {
				return false
			}
			for _, ch := range item.Name {
				if unicode.IsControl(ch) {
					return false
				}
			}
			seen[item.ID] = true
		}
	}
	seen := map[string]bool{}
	for _, action := range c.Actions {
		if !pcActions[action] || seen[action] {
			return false
		}
		seen[action] = true
	}
	return true
}
func (p *pcPeer) write(v any) error {
	return p.writeContext(context.Background(), time.Now().Add(5*time.Second), v)
}
func (p *pcPeer) writeContext(ctx context.Context, deadline time.Time, v any) error {
	raw, err := json.Marshal(v)
	if err != nil {
		return err
	}
	if len(raw) > pcMaxFrame {
		return errors.New("computer frame too large")
	}
	p.writeMu.Lock()
	defer p.writeMu.Unlock()
	if err := ctx.Err(); err != nil {
		return err
	}
	if !deadline.After(time.Now()) {
		return context.DeadlineExceeded
	}
	p.mu.Lock()
	closed := p.closed
	p.mu.Unlock()
	if closed {
		return errors.New("computer offline")
	}
	_ = p.conn.SetWriteDeadline(minTime(deadline, time.Now().Add(5*time.Second)))
	return p.conn.WriteMessage(websocket.TextMessage, raw)
}
func minTime(a, b time.Time) time.Time {
	if a.Before(b) {
		return a
	}
	return b
}
func (p *pcPeer) ping() {
	ticker := time.NewTicker(p.relay.pingInterval)
	defer ticker.Stop()
	for {
		select {
		case <-p.done:
			return
		case <-ticker.C:
			if p.conn.WriteControl(websocket.PingMessage, nil, time.Now().Add(5*time.Second)) != nil {
				p.close()
				return
			}
		}
	}
}
func (p *pcPeer) close() {
	p.closeOnce.Do(func() {
		p.mu.Lock()
		p.closed = true
		p.ready = false
		for id, wait := range p.pending {
			wait.done <- pcOutcome{err: errors.New("computer disconnected; execution outcome unknown; not retried")}
			delete(p.pending, id)
		}
		p.mu.Unlock()
		_ = p.conn.Close()
		close(p.done)
		p.relay.mu.Lock()
		if p.relay.peer == p {
			p.relay.peer = nil
		}
		p.relay.mu.Unlock()
	})
}
func (r *pcRelay) connected(device string) *pcPeer {
	if !r.authorized(device) {
		return nil
	}
	r.mu.Lock()
	p := r.peer
	r.mu.Unlock()
	if p != nil {
		p.mu.Lock()
		ready := p.ready && !p.closed
		p.mu.Unlock()
		if ready {
			return p
		}
	}
	return nil
}
func (r *pcRelay) call(ctx context.Context, device, action string, args map[string]any) (pcResult, error) {
	if !r.authorizedContext(ctx, device) {
		return pcResult{}, errors.New("computer access is not authorized for this robot")
	}
	p := r.connected(device)
	if p == nil {
		return pcResult{}, errors.New("computer offline; action was not queued")
	}
	if err := validatePCAction(p.caps, action, args); err != nil {
		return pcResult{}, err
	}
	if err := ctx.Err(); err != nil {
		return pcResult{}, err
	}
	deadline := time.Now().Add(min(r.timeout, 15*time.Second))
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	if !deadline.After(time.Now()) {
		return pcResult{}, context.DeadlineExceeded
	}
	idBytes := make([]byte, 16)
	if _, err := rand.Read(idBytes); err != nil {
		return pcResult{}, errors.New("cannot generate computer request id")
	}
	id := hex.EncodeToString(idBytes)
	wait := &pcPending{done: make(chan pcOutcome, 1), deadline: deadline}
	p.mu.Lock()
	if p.closed || !p.ready {
		p.mu.Unlock()
		return pcResult{}, errors.New("computer offline; action was not queued")
	}
	if len(p.pending) >= pcMaxPending {
		p.mu.Unlock()
		return pcResult{}, errors.New("computer busy; action was not queued")
	}
	p.pending[id] = wait
	p.mu.Unlock()
	defer func() { p.mu.Lock(); delete(p.pending, id); p.mu.Unlock() }()
	if err := p.writeContext(ctx, deadline, map[string]any{"type": "call", "id": id, "action": action, "args": args, "deadline_ms": deadline.UnixMilli()}); err != nil {
		p.close()
		return pcResult{}, errors.New("computer connection lost; execution outcome unknown; not retried")
	}
	timer := time.NewTimer(time.Until(deadline))
	defer timer.Stop()
	select {
	case outcome := <-wait.done:
		return outcome.result, outcome.err
	case <-ctx.Done():
		return pcResult{}, fmt.Errorf("computer request cancelled; execution outcome unknown; not retried: %w", ctx.Err())
	case <-timer.C:
		return pcResult{}, errors.New("computer action timed out; execution outcome unknown; not retried")
	}
}
