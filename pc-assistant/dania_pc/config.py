from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ACTIONS = frozenset({"open_app", "open_project", "set_volume", "media", "search_web", "lock", "codex_start", "codex_status", "codex_cancel"})
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
    codex: dict | None = None

    def hello(self):
        return {"type": "hello", "version": 1, "pc_id": self.pc_id,
                "capabilities": {"apps": [{"id": k, "name": v["name"]} for k, v in self.apps.items()]
                                 if "open_app" in self.actions else [],
                                 "projects": [{"id": k, "name": v["name"]} for k, v in self.projects.items()]
                                 if self.actions & {"open_project", "codex_start"} else [],
                                 "actions": sorted(self.actions)}}


def load_config(path: Path) -> Config:
    raw = json.loads(path.read_text(encoding="utf-8"))
    allowed = {"endpoint", "token", "pc_id", "actions", "apps", "projects", "dry_run", "state_file", "codex"}
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
    codex = raw.get("codex")
    if codex is not None:
        if not isinstance(codex, dict) or codex.keys() - {"executable", "tasks_file", "sandbox", "timeout_seconds", "home", "windows_sandbox"}:
            raise ValueError("Invalid Codex configuration")
        executable = codex.get("executable")
        if not isinstance(executable, str) or not Path(executable).is_absolute() or Path(executable).suffix.lower() != ".exe":
            raise ValueError("Configure an absolute Codex executable")
        sandbox = codex.get("sandbox", "read-only")
        timeout = codex.get("timeout_seconds", 1800)
        if sandbox not in {"read-only", "workspace-write"} or type(timeout) is not int or not 10 <= timeout <= 7200:
            raise ValueError("Invalid Codex sandbox or timeout")
        tasks = codex.get("tasks_file", "private/codex-tasks.json")
        if not isinstance(tasks, str) or not tasks:
            raise ValueError("Invalid Codex tasks file")
        tasks = Path(tasks)
        if not tasks.is_absolute(): tasks = path.resolve().parent / tasks
        schema_path = tasks.with_name(tasks.name + ".result-schema.json")
        if tasks.resolve() in {path.resolve(), state_file.resolve()} or schema_path.resolve() in {path.resolve(), state_file.resolve()}:
            raise ValueError("Codex task storage must be separate")
        home = codex.get("home")
        if home is not None:
            if not isinstance(home, str) or not Path(home).is_absolute() or not Path(home).is_dir():
                raise ValueError("Codex home must be an existing absolute directory")
            home = Path(home).resolve()
            for item in groups["projects"].values():
                if home == Path(item["path"]).resolve() or Path(item["path"]).resolve() in home.parents:
                    raise ValueError("Keep Codex authentication outside allowed projects")
        windows_sandbox = codex.get("windows_sandbox")
        if windows_sandbox is not None and (not isinstance(windows_sandbox, str) or windows_sandbox not in {"mxc", "elevated", "unelevated"}):
            raise ValueError("Invalid Windows sandbox backend")
        codex = {"executable": executable, "sandbox": sandbox, "timeout_seconds": timeout, "tasks_file": tasks.resolve(), "home": home, "windows_sandbox": windows_sandbox}
    if set(actions) & {"codex_start", "codex_status", "codex_cancel"} and (codex is None or not groups["projects"]):
        raise ValueError("Codex actions require configuration and allowed projects")
    config = Config(endpoint, token, pc_id, frozenset(actions), groups["apps"], groups["projects"],
                    dry_run, state_file, codex)
    if len(json.dumps(config.hello()).encode("utf-8")) > 16 * 1024:
        raise ValueError("Advertised capabilities exceed the protocol frame limit")
    return config
