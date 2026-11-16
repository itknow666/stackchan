from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ACTIONS = frozenset({"open_app", "open_project", "set_volume", "media", "search_web", "lock"})
ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


@dataclass(frozen=True)
class Config:
    endpoint: str
    token: str
    pc_id: str
    actions: frozenset[str]
    apps: dict
    projects: dict
    dry_run: bool
    state_file: Path

    def hello(self):
        return {"type": "hello", "version": 1, "pc_id": self.pc_id,
                "capabilities": {"apps": [{"id": k, "name": v["name"]} for k, v in self.apps.items()]
                                 if "open_app" in self.actions else [],
                                 "projects": [{"id": k, "name": v["name"]} for k, v in self.projects.items()]
                                 if "open_project" in self.actions else [],
                                 "actions": sorted(self.actions)}}


def load_config(path: Path) -> Config:
    raw = json.loads(path.read_text(encoding="utf-8"))
    allowed = {"endpoint", "token", "pc_id", "actions", "apps", "projects", "dry_run", "state_file"}
    if not isinstance(raw, dict) or raw.keys() - allowed:
        raise ValueError("Unknown configuration fields")
    endpoint, token = raw.get("endpoint", ""), raw.get("token", "")
    if not isinstance(endpoint, str) or not isinstance(token, str):
        raise ValueError("Endpoint and token must be strings")
    url = urlsplit(endpoint)
    if (url.scheme != "wss" or not url.hostname or url.username or url.password or
            url.path != "/dania/pc/ws" or url.query or url.fragment):
        raise ValueError("Use a WSS endpoint ending in /dania/pc/ws without URL credentials")
    if len(token) < 32 or len(token) > 4096 or any(c.isspace() for c in token):
        raise ValueError("Token must contain 32 to 4096 non-whitespace characters")
    pc_id = raw.get("pc_id", "desktop")
    if not isinstance(pc_id, str) or not ID.fullmatch(pc_id):
        raise ValueError("Invalid pc_id")
    actions = raw.get("actions", [])
    if not isinstance(actions, list) or any(not isinstance(a, str) or a not in ACTIONS for a in actions):
        raise ValueError("Invalid allowed actions")
    if len(actions) != len(set(actions)):
        raise ValueError("Duplicate allowed action")
    groups = {}
    for key in ("apps", "projects"):
        entries = raw.get(key, [])
        if not isinstance(entries, list) or len(entries) > 64:
            raise ValueError("Allowlist must contain at most 64 entries")
        group = {}
        fields = {"id", "name", "executable", "args"} if key == "apps" else {"id", "name", "path"}
        for item in entries:
            if not isinstance(item, dict) or item.keys() - fields:
                raise ValueError("Invalid allowlist entry")
            item_id, name = item.get("id"), item.get("name")
            if (not isinstance(item_id, str) or not ID.fullmatch(item_id) or item_id in group or
                    not isinstance(name, str) or not 1 <= len(name) <= 100):
                raise ValueError("Invalid allowlist identity")
            target = item.get("executable" if key == "apps" else "path")
            if not isinstance(target, str) or not Path(target).is_absolute():
                raise ValueError("Allowlist paths must be absolute")
            if key == "apps":
                args = item.get("args", [])
                if (not isinstance(args, list) or len(args) > 32 or
                        any(not isinstance(a, str) or len(a) > 2048 or "\x00" in a for a in args)):
                    raise ValueError("Invalid fixed application arguments")
                # Scripts and interpreters would turn a fixed application into a remote shell surface.
                if Path(target).suffix.lower() != ".exe":
                    raise ValueError("Configure an executable, not a script")
            group[item_id] = dict(item)
        groups[key] = group
    dry_run = raw.get("dry_run", True)
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be boolean")
    state = raw.get("state_file", "private/status.json")
    if not isinstance(state, str) or not state:
        raise ValueError("Invalid state_file")
    state_file = Path(state)
    if not state_file.is_absolute():
        state_file = path.resolve().parent / state_file
    if state_file.resolve() == path.resolve():
        raise ValueError("State file must differ from the configuration file")
    config = Config(endpoint, token, pc_id, frozenset(actions), groups["apps"], groups["projects"],
                    dry_run, state_file)
    if len(json.dumps(config.hello()).encode("utf-8")) > 16 * 1024:
        raise ValueError("Advertised capabilities exceed the protocol frame limit")
    return config
