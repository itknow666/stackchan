import asyncio
import tempfile
import unittest
from pathlib import Path

from dania_pc.config import Config
from dania_pc.desktop_tasks import DesktopCodexTasks


class Bridge:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.completed = False
        self.fail_create = False
        self.final = "任务完成，已核对当前工作区。"

    async def call(self, name, args):
        self.calls.append((name, args))
        if name == "list_projects":
            return {"projects": [{"projectKind": "local", "projectId": "profile",
                                  "path": str(self.root)}]}
        if name == "create_thread":
            if self.fail_create: raise TimeoutError()
            return {"threadId": f"chat-{len(self.calls)}", "hostId": "local"}
        if name == "read_thread":
            return {"thread": {"id": args["threadId"]},
                    "turns": [{"status": "completed" if self.completed else "inProgress",
                               "items": [{"type": "agentMessage", "phase": "final_answer",
                                          "text": self.final}]}]}

    async def close(self): pass


class DesktopTasks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = Config("wss://example.com/dania/pc/ws", "x"*32, "pc",
            frozenset({"codex_start", "codex_status", "codex_cancel"}), {},
            {"profile": {"name": "Profile", "path": str(self.root)}}, False,
            self.root/"status.json", {"backend": "desktop", "desktop_workspace": str(self.root),
                                     "tasks_file": self.root/"tasks.json"})
        self.bridge = Bridge(self.root)
        self.jobs = DesktopCodexTasks(self.config, self.bridge)
        self.addAsyncCleanup(self.jobs.close)

    async def start(self):
        return await self.jobs.execute("codex_start", {"project": "profile", "task": "用户原话\n检查目录"})

    async def test_each_request_is_new_visible_local_chat(self):
        _, first = await self.start()
        _, second = await self.start()
        self.assertNotEqual(first["thread_id"], second["thread_id"])
        creates = [args for name, args in self.bridge.calls if name == "create_thread"]
        self.assertEqual(len(creates), 2)
        self.assertEqual(creates[0]["target"], {"type": "project", "projectId": "profile",
                                               "environment": {"type": "local"}})
        self.assertIn("用户原话", creates[0]["prompt"])
        self.assertNotIn("model", creates[0])
        self.assertNotIn("summary", self.jobs.projection()[0])

    async def test_submission_unknown_never_retries_or_claims_success(self):
        self.bridge.fail_create = True
        with self.assertRaises(TimeoutError): await self.start()
        self.assertEqual(self.jobs.tasks[-1]["state"], "interrupted")
        self.assertEqual(sum(n == "create_thread" for n, _ in self.bridge.calls), 1)

    async def test_monitor_real_final_and_restart_does_not_resubmit(self):
        _, accepted = await self.start()
        await self.jobs.close()
        restored = DesktopCodexTasks(self.config, self.bridge)
        self.addAsyncCleanup(restored.close)
        self.bridge.completed = True
        restored.resume_monitors()
        await asyncio.gather(*restored.monitors.values())
        self.assertEqual(restored.tasks[-1]["state"], "completed")
        self.assertEqual(sum(n == "create_thread" for n, _ in self.bridge.calls), 1)

    async def test_blocked_answer_is_not_success_and_cancel_honest(self):
        await self.start()
        text, result = await self.jobs.execute("codex_cancel", {"task_id": "latest"})
        self.assertIn("桌面", text)
        self.assertEqual(result["state"], "running")
        await self.jobs.close()
        self.bridge.completed = True
        self.bridge.final = "需要补充信息，请提供项目名称。"
        self.jobs.resume_monitors()
        await asyncio.gather(*self.jobs.monitors.values())
        self.assertEqual(self.jobs.tasks[-1]["state"], "interrupted")

    async def test_finishing_an_older_chat_still_appears_in_notification_projection(self):
        self.jobs.tasks = [{"task_id": str(i), "project": "profile", "state": "completed",
                            "created_at_ms": i, "finished_at_ms": i} for i in range(30)]
        self.jobs.tasks[0]["finished_at_ms"] = 1000
        self.assertEqual(self.jobs.projection()[-1]["task_id"], "0")
