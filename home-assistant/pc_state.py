"""Read the companion's bounded, fresh status snapshot once per refresh."""
import json
import time


def read_pc_state(path, now=None):
    now = time.time() * 1000 if now is None else now
    try:
        if path.stat().st_size > 32768:
            return None
        state = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(state, dict) or state.get('version') != 1 or
                type(state.get('updated_at_ms')) is not int or
                not 0 <= now - state['updated_at_ms'] <= 15000 or
                type(state.get('connected')) is not bool or
                type(state.get('dry_run')) is not bool):
            return None
        return state
    except (OSError, ValueError):
        return None
