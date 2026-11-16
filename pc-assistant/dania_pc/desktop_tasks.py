"""Visible desktop chats; stopping this companion never kills the user's Codex."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from .codex_tasks import atomic_json, TERMINAL
from .desktop_mcp import DesktopMCP


class DesktopCodexTasks:
    def __init__(self, config, bridge=None):
        self.config = config
        self.path = Path(config.codex["tasks_file"])
        self.bridge = bridge or DesktopMCP(config.codex)
        self.tasks = []
        self.monitors = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.tasks = data["tasks"]
            for task in self.tasks:
                if task["state"] not in TERMINAL and not task.get("thread_id"):
                    task.update(state="interrupted", summary="旧任务没有桌面聊天编号，请在电脑上核对。",
                                finished_at_ms=self.now())
            self.save()

    @staticmethod
    def now(): return int(time.time() * 1000)

    def save(self):
        atomic_json(self.path, {"version": 1, "tasks": self.tasks, "sessions": {}})

    def resume_monitors(self):
        for task in self.tasks:
            identity = task["task_id"]
            if task["state"] not in TERMINAL and task.get("thread_id") and identity not in self.monitors:
                self.monitors[identity] = asyncio.create_task(self.monitor(task))

    def projection(self):
        self.resume_monitors()
        recent = sorted(self.tasks, key=lambda t: t.get("finished_at_ms", t["created_at_ms"]))[-20:]
        return [{k: t[k] for k in ("task_id", "project", "state", "created_at_ms", "finished_at_ms")
                 if k in t} for t in recent]

    async def execute(self, action, args):
        self.resume_monitors()
        if action == "codex_start":
            if len([t for t in self.tasks if t["state"] not in TERMINAL]) >= 4:
                return "已有四条任务在进行，请先查看桌面。", {"accepted": False}
            workspace = Path(self.config.codex["desktop_workspace"]).resolve(strict=True)
            projects = await self.bridge.call("list_projects", {})
            project = next((p for p in projects["projects"]
                            if p.get("projectKind") == "local" and
                            Path(p.get("path", "")).resolve() == workspace), None)
            if project is None:
                raise ValueError("Configured workspace is not registered in Codex desktop")
            task = {"task_id": uuid.uuid4().hex, "project": args["project"],
                    "state": "accepted", "created_at_ms": self.now(),
                    "summary": "正在创建桌面任务。"}
            self.tasks = [t for t in self.tasks if t["state"] not in TERMINAL] + [
                t for t in self.tasks if t["state"] in TERMINAL][-95:] + [task]
            self.save()
            try:
                lines = [line.strip() for line in args["task"].splitlines() if line.strip()]
                title = lines[1] if lines and lines[0] == "用户语音原话" and len(lines) > 1 else lines[0]
                result = await self.bridge.call("create_thread", {
                    "target": {"type": "project", "projectId": project["projectId"],
                               "environment": {"type": "local"}},
                    "title": title[:60],
                    "prompt": args["task"] + "\n\n最后回复请以任务完成、需要补充信息或执行失败开头，随后说明实际结果和限制。只有目标确实完成并核对后才能写任务完成。"})
                if not result.get("threadId"):
                    raise RuntimeError("Desktop did not return a ready local chat ID")
                task.update(thread_id=result["threadId"], host_id=result.get("hostId", "local"),
                            state="running", summary="已在 Codex 桌面新建聊天并开始处理。")
                self.save()
                self.resume_monitors()
            except BaseException:
                task.update(state="interrupted", finished_at_ms=self.now(),
                            summary="桌面提交结果未确认，请查看 Codex，系统不会自动重复提交。")
                self.save()
                raise
            return task["summary"], {"accepted": True, "task_id": task["task_id"],
                                      "state": task["state"], "thread_id": task["thread_id"]}
        task = next((t for t in reversed(self.tasks)
                     if args["task_id"] in {"latest", t["task_id"]}), None)
        if task is None:
            raise ValueError("Unknown Codex task")
        if action == "codex_cancel" and task["state"] not in TERMINAL:
            return "请在这条 Codex 桌面聊天中点停止；当前桌面接口不支持远程取消。", {
                "task_id": task["task_id"], "state": task["state"]}
        return "Codex 桌面任务状态", {k: task[k] for k in
            ("task_id", "project", "state", "summary", "thread_id") if k in task}

    async def monitor(self, task):
        try:
            while True:
                try:
                    result = await self.bridge.call("read_thread", {
                        "threadId": task["thread_id"], "hostId": task["host_id"],
                        "turnLimit": 1, "includeOutputs": False})
                    turn = next(iter(result.get("turns", [])), {})
                    status = turn.get("status")
                    if status in {"completed", "failed", "interrupted"}:
                        message = next((item for item in reversed(turn.get("items", []))
                                        if item.get("type") == "agentMessage" and
                                        item.get("phase") == "final_answer"), {})
                        text = message.get("text", "")
                        state = status
                        if status == "completed" and not text.lstrip("*# \n").startswith("任务完成"):
                            state = "interrupted"
                        task.update(state=state, summary=text[-3000:] or
                                    "桌面任务已结束，请查看对应聊天。",
                                    finished_at_ms=int(turn["completedAt"] * 1000)
                                    if isinstance(turn.get("completedAt"), (int, float)) else self.now())
                        self.save()
                        return
                    if result.get("thread", {}).get("status", {}).get("type") == "needsAttention":
                        task["summary"] = "Codex 需要你在电脑上补充信息或确认操作。"
                        self.save()
                except (ConnectionError, RuntimeError, TimeoutError, OSError):
                    # A disconnected observer says nothing about the actual desktop task.
                    task["summary"] = "暂时无法读取桌面任务进展，请查看 Codex；不会重复提交。"
                    self.save()
                await asyncio.sleep(5)
        finally:
            self.monitors.pop(task["task_id"], None)

    async def close(self):
        monitors = list(self.monitors.values())
        for monitor in monitors:
            monitor.cancel()
        await asyncio.gather(*monitors, return_exceptions=True)
        self.monitors.clear()
        await self.bridge.close()
