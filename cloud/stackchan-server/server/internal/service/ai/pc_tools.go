package ai

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

type pcControl struct {
	relay    *pcRelay
	deviceID string
	grant    *pcGrant
}
type pcMemo struct {
	text string
	err  error
}
type pcTurn struct {
	calls int
	memo  map[string]pcMemo
}

func pcControlFor(ctx context.Context, r *pcRelay, deviceID string) *pcControl {
	if !r.authorizedContext(ctx, deviceID) {
		return nil
	}
	grant, _ := ctx.Value(pcGrantKey{}).(*pcGrant)
	return &pcControl{relay: r, deviceID: deviceID, grant: grant}
}
func hasPCAction(c pcCapabilities, action string) bool {
	for _, allowed := range c.Actions {
		if action == allowed {
			return true
		}
	}
	return false
}
func pcItemIDs(items []pcItem) []string {
	ids := make([]string, 0, len(items))
	for _, item := range items {
		ids = append(ids, item.ID)
	}
	return ids
}
func pcEnum(field string, values []string) map[string]any {
	return map[string]any{field: map[string]any{"type": "string", "enum": values}}
}

func (p *pcControl) tools() []map[string]any {
	if p == nil || p.grant == nil || p.grant.relay != p.relay || p.grant.deviceID != p.deviceID {
		return nil
	}
	peer := p.relay.connected(p.deviceID)
	if peer == nil {
		return nil
	}
	c := peer.caps
	tools := []map[string]any{}
	for _, action := range []string{"open_app", "open_project", "set_volume", "media", "search_web", "lock", "codex_start", "codex_status", "codex_cancel"} {
		if !hasPCAction(c, action) {
			continue
		}
		props := map[string]any{}
		required := []string{}
		description := ""
		switch action {
		case "open_app", "open_project":
			items := c.Apps
			description = "Request opening an allowed application on the computer."
			if action == "open_project" {
				items = c.Projects
				description = "Request opening an allowed project folder on the computer."
			}
			if len(items) == 0 {
				continue
			}
			props = pcEnum("id", pcItemIDs(items))
			required = []string{"id"}
			listing, _ := json.Marshal(items)
			description += " Allowed IDs and display names: " + string(listing)
		case "set_volume":
			props["value"] = map[string]any{"type": "integer", "minimum": 0, "maximum": 100}
			required = []string{"value"}
			description = "Set computer volume from zero to one hundred, separate from robot volume."
		case "media":
			props = pcEnum("command", []string{"play_pause", "next", "previous"})
			required = []string{"command"}
			description = "Send a computer media key once. play_pause toggles playback; do not repeat it to check status."
		case "search_web":
			props["query"] = map[string]any{"type": "string", "minLength": 1, "maxLength": 300}
			required = []string{"query"}
			description = "Open a web search in the computer browser; this tool does not return search results."
		case "codex_start":
			if len(c.Projects) == 0 {
				continue
			}
			props = pcEnum("project", pcItemIDs(c.Projects))
			props["task"] = map[string]any{"type": "string", "minLength": 1, "maxLength": 4000}
			required = []string{"project", "task"}
			listing, _ := json.Marshal(c.Projects)
			description = "Start a NEW visible Codex desktop chat ONLY when the user explicitly asks Codex to do a task. Allowed projects: " + string(listing) + ". Use profile by default when available. Each request creates a separate desktop chat in the configured workspace. The service preserves the current user's exact words and uses DeepSeek to clarify the task before submission. Never add constraints the user did not request, especially a prohibition on web research. Acceptance is not completion. Save task_id; query status rather than submitting again. Desktop permissions and model settings remain under user control."
		case "codex_status", "codex_cancel":
			props["task_id"] = map[string]any{"type": "string", "minLength": 1, "maxLength": 32}
			required = []string{"task_id"}
			description = "Query a background Codex task; use returned task_id or latest for the latest task."
			if action == "codex_cancel" {
				description = "Request cancellation of a Codex task; use task_id or latest. Cancellation does not undo existing file edits. Query status to confirm it stopped."
			}
		case "lock":
			description = "Ask Windows to lock the computer."
		}
		description += " Wait for the actual result before reporting success. A simulated result performed no desktop action. A timeout or disconnect means outcome unknown; do not retry automatically."
		tools = append(tools, map[string]any{"type": "function", "function": map[string]any{"name": "pc_" + action, "description": description, "parameters": map[string]any{"type": "object", "properties": props, "required": required, "additionalProperties": false}}})
	}
	return tools
}

func validatePCAction(c pcCapabilities, action string, args map[string]any) error {
	if !hasPCAction(c, action) {
		return errors.New("computer action is not allowed")
	}
	if args == nil {
		return errors.New("computer arguments must be an object")
	}
	if action == "codex_start" {
		if len(args) != 2 {
			return errors.New("Codex start expects project and task")
		}
		project, projectOK := args["project"].(string)
		task, taskOK := args["task"].(string)
		if !projectOK || !taskOK || !utf8.ValidString(task) || utf8.RuneCountInString(strings.TrimSpace(task)) < 1 || utf8.RuneCountInString(task) > 4000 {
			return errors.New("invalid Codex task")
		}
		for _, ch := range task {
			if unicode.IsControl(ch) && ch != '\n' && ch != '\t' {
				return errors.New("invalid Codex task text")
			}
		}
		for _, item := range c.Projects {
			if item.ID == project {
				return nil
			}
		}
		return errors.New("unknown Codex project")
	}
	if action == "codex_status" || action == "codex_cancel" {
		id, ok := args["task_id"].(string)
		if len(args) != 1 || !ok || (id != "latest" && !regexp.MustCompile(`^[a-f0-9]{32}$`).MatchString(id)) {
			return errors.New("invalid Codex task ID")
		}
		return nil
	}
	field := map[string]string{"open_app": "id", "open_project": "id", "set_volume": "value", "media": "command", "search_web": "query", "lock": ""}[action]
	if !pcActions[action] {
		return errors.New("unknown computer action")
	}
	if field == "" {
		if len(args) != 0 {
			return errors.New("unexpected computer arguments")
		}
		return nil
	}
	if len(args) != 1 {
		return errors.New("computer arguments do not match the action")
	}
	value, ok := args[field]
	if !ok {
		return errors.New("missing computer argument")
	}
	switch action {
	case "open_app", "open_project":
		id, ok := value.(string)
		if !ok {
			return errors.New("invalid computer allowlist id")
		}
		items := c.Apps
		if action == "open_project" {
			items = c.Projects
		}
		for _, item := range items {
			if item.ID == id {
				return nil
			}
		}
		return errors.New("unknown computer allowlist id")
	case "set_volume":
		n, ok := value.(float64)
		if !ok || math.IsNaN(n) || math.IsInf(n, 0) || n < 0 || n > 100 || math.Trunc(n) != n {
			return errors.New("computer volume must be an integer from zero to one hundred")
		}
	case "media":
		command, ok := value.(string)
		if !ok || (command != "play_pause" && command != "next" && command != "previous") {
			return errors.New("unknown computer media command")
		}
	case "search_web":
		query, ok := value.(string)
		if !ok || !utf8.ValidString(query) || utf8.RuneCountInString(strings.TrimSpace(query)) < 1 || utf8.RuneCountInString(strings.TrimSpace(query)) > 300 {
			return errors.New("computer search query must contain one to three hundred characters")
		}
		for _, ch := range query {
			if !unicode.IsPrint(ch) {
				return errors.New("computer search query contains non-printable characters")
			}
		}
	}
	return nil
}

func (p *pcControl) dispatch(ctx context.Context, turn *pcTurn, name string, args map[string]any) (string, error) {
	if p == nil || p.grant == nil || p.grant.relay != p.relay || p.grant.deviceID != p.deviceID || !p.relay.authorizedContext(ctx, p.deviceID) {
		return "", errors.New("computer access is not authorized for this robot")
	}
	if turn.memo == nil {
		turn.memo = map[string]pcMemo{}
	}
	raw, err := json.Marshal(args)
	if err != nil {
		return "", errors.New("invalid computer arguments")
	}
	key := name + ":" + string(raw)
	if memo, ok := turn.memo[key]; ok {
		return memo.text, memo.err
	}
	if turn.calls >= 4 {
		return "", errors.New("computer action limit reached for this turn; do not retry")
	}
	turn.calls++
	result, err := p.relay.call(ctx, p.deviceID, strings.TrimPrefix(name, "pc_"), args)
	text := ""
	if err == nil {
		encoded, _ := json.Marshal(result)
		text = string(encoded)
	}
	turn.memo[key] = pcMemo{text: text, err: err}
	return text, err
}
