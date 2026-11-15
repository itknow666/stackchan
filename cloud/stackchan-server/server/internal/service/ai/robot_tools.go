package ai

import (
	"fmt"
	"math"
	"strings"
	"unicode"
)

type RobotPose struct {
	Yaw   float64 `json:"yaw"`
	Pitch float64 `json:"pitch"`
	Roll  float64 `json:"roll"`
}
type robotControl struct {
	emotion func(string) error
	pose    func(RobotPose) error
}

var robotEmotions = []string{"NEUTRAL", "HAPPY", "ANGRY", "SAD", "DOUBTFUL", "SLEEPY", "COLD", "HOT"}

func robotToolDefs() []map[string]any {
	wrap := func(name, description string, props map[string]any, required []string) map[string]any {
		return map[string]any{"type": "function", "function": map[string]any{"name": name, "description": description,
			"parameters": map[string]any{"type": "object", "properties": props, "required": required, "additionalProperties": false}}}
	}
	number := func(min, max float64) map[string]any {
		return map[string]any{"type": "number", "minimum": min, "maximum": max}
	}
	return []map[string]any{
		wrap("set_emotion", "Set the robot face before speaking. Never describe this tool in spoken text.", map[string]any{"emotion": map[string]any{"type": "string", "enum": robotEmotions}}, []string{"emotion"}),
		wrap("set_pose", "Set a gentle head pose in radians. Positive yaw is left, positive pitch is down. This device has NO roll axis: roll must be zero. The head returns to neutral after playback.", map[string]any{"yaw": number(-.5, .5), "pitch": number(-.3, .3), "roll": number(0, 0)}, []string{"yaw", "pitch", "roll"}),
	}
}

func (r *robotControl) dispatch(name string, args map[string]any) (string, error) {
	if r == nil {
		return "", fmt.Errorf("robot tools unavailable")
	}
	switch name {
	case "set_emotion":
		value, ok := args["emotion"].(string)
		if !ok || len(args) != 1 {
			return "", fmt.Errorf("provide only emotion")
		}
		valid := false
		for _, e := range robotEmotions {
			if value == e {
				valid = true
				break
			}
		}
		if !valid || r.emotion == nil {
			return "", fmt.Errorf("unsupported emotion")
		}
		if err := r.emotion(strings.ToLower(value)); err != nil {
			return "", err
		}
	case "set_pose":
		y, yok := args["yaw"].(float64)
		p, pok := args["pitch"].(float64)
		v, rok := args["roll"].(float64)
		if !yok || !pok || !rok || len(args) != 3 || math.IsNaN(y) || math.IsInf(y, 0) || math.IsNaN(p) || math.IsInf(p, 0) || math.Abs(y) > .5 || math.Abs(p) > .3 || v != 0 || r.pose == nil {
			return "", fmt.Errorf("yaw must be within -0.5..0.5, pitch -0.3..0.3; roll is unavailable and must be zero")
		}
		if err := r.pose(RobotPose{Yaw: y, Pitch: p, Roll: 0}); err != nil {
			return "", err
		}
	default:
		return "", fmt.Errorf("unknown robot tool")
	}
	return "Applied for the upcoming spoken reply", nil
}

// The user explicitly requested speech without punctuation, symbols or markup.
func spokenRobotText(s string) string {
	s = strings.Map(func(r rune) rune {
		if unicode.IsPunct(r) || unicode.IsSymbol(r) {
			return ' '
		}
		return r
	}, s)
	return strings.Join(strings.Fields(s), " ")
}
