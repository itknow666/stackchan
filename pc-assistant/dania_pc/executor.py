from __future__ import annotations

import ctypes
import os
import re
import subprocess
from urllib.parse import urlencode

from .config import Config
from .codex_tasks import CodexTasks


def validate_action(config: Config, action, args):
    if not isinstance(action, str) or action not in config.actions:
        raise ValueError("Action is not allowed on this PC")
    if not isinstance(args, dict):
        raise ValueError("Arguments must be an object")
    fields = {"open_app": {"id"}, "open_project": {"id"}, "set_volume": {"value"},
              "media": {"command"}, "search_web": {"query"}, "lock": set(), "codex_start": {"project", "task"},
              "codex_status": {"task_id"}, "codex_cancel": {"task_id"}}[action]
    if args.keys() != fields:
        raise ValueError("Arguments do not match the action")
    if action == "codex_start":
        if not config.codex or not isinstance(args["project"], str) or args["project"] not in config.projects:
            raise ValueError("Unknown Codex project")
        task = args["task"]
        if not isinstance(task, str) or not 1 <= len(task.strip()) <= 4000 or any(ord(c) < 32 and c not in "\n\t" for c in task):
            raise ValueError("Codex task must contain one to four thousand characters")
    elif action in {"codex_status", "codex_cancel"}:
        if not config.codex or not isinstance(args["task_id"], str) or (args["task_id"] != "latest" and not re.fullmatch(r"[a-f0-9]{32}", args["task_id"])):
            raise ValueError("Invalid Codex task ID")
    elif action in {"open_app", "open_project"}:
        entries = config.apps if action == "open_app" else config.projects
        if not isinstance(args["id"], str) or args["id"] not in entries:
            raise ValueError("Unknown local allowlist id")
    elif action == "set_volume":
        if type(args["value"]) is not int or not 0 <= args["value"] <= 100:
            raise ValueError("Volume must be an integer from 0 to 100")
    elif action == "media":
        if args["command"] not in ("play_pause", "next", "previous"):
            raise ValueError("Unknown media command")
    elif action == "search_web":
        query = args["query"]
        if (not isinstance(query, str) or not 1 <= len(query.strip()) <= 300 or
                not query.isprintable()):
            raise ValueError("Search query must contain 1 to 300 printable characters")


class NativeExecutor:
    """Native calls are short and synchronous: no shell, no threads left after disconnect."""

    def __init__(self, config: Config):
        self.config = config
        self.codex_tasks = CodexTasks(config) if config.codex else None

    async def execute(self, action, args, before_effect=lambda: None):
        validate_action(self.config, action, args)
        if self.config.dry_run:
            before_effect()
            return "Simulated; no desktop action was performed", {"simulated": True}
        if action.startswith("codex_"):
            before_effect()
            return await self.codex_tasks.execute(action, args)
        if os.name != "nt":
            raise RuntimeError("Desktop execution requires Windows")
        if action == "open_app":
            app = self.config.apps[args["id"]]
            before_effect()
            subprocess.Popen([app["executable"], *app.get("args", [])], shell=False,
                             close_fds=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "Application launch requested", {}
        if action == "open_project":
            folder = self.config.projects[args["id"]]["path"]
            if not os.path.isdir(folder):
                raise RuntimeError("Configured folder is unavailable")
            before_effect()
            os.startfile(folder, "explore")
            return "Project folder open requested", {}
        if action == "set_volume":
            import comtypes
            from pycaw.pycaw import AudioUtilities
            comtypes.CoInitialize()
            try:
                volume = AudioUtilities.GetSpeakers().EndpointVolume
                before_effect()
                volume.SetMasterVolumeLevelScalar(args["value"] / 100, None)
                actual = round(volume.GetMasterVolumeLevelScalar() * 100)
                return "Computer volume changed", {"value": actual}
            finally:
                comtypes.CoUninitialize()
        if action == "media":
            key = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1}[args["command"]]
            before_effect()
            ctypes.windll.user32.keybd_event(key, 0, 0, 0)
            ctypes.windll.user32.keybd_event(key, 0, 2, 0)
            return "Media key sent", {}
        if action == "lock":
            before_effect()
            if not ctypes.windll.user32.LockWorkStation():
                raise RuntimeError("Windows refused the lock request")
            return "Windows accepted the lock request", {}
        if action == "search_web":
            url = "https://www.bing.com/search?" + urlencode({"q": args["query"].strip()})
            before_effect()
            os.startfile(url)
            return "Browser search open requested", {}
        raise ValueError("Unknown action")
