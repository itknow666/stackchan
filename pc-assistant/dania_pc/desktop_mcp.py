"""Standard MCP client for the installed official Codex desktop app tools."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess


class DesktopMCP:
    def __init__(self, options):
        self.options = options
        self.process = None
        self.lock = asyncio.Lock()
        self.sequence = 0

    async def rpc(self, method, params):
        self.sequence += 1
        identity = self.sequence
        self.process.stdin.write((json.dumps(
            {"jsonrpc": "2.0", "id": identity, "method": method, "params": params},
            ensure_ascii=False) + "\n").encode())
        await self.process.stdin.drain()
        while line := await self.process.stdout.readline():
            response = json.loads(line)
            if response.get("id") != identity:
                continue
            if "error" in response:
                raise RuntimeError(response["error"].get("message", "Desktop MCP failed"))
            return response["result"]
        raise ConnectionError("Codex desktop MCP disconnected")

    async def connect(self):
        if self.process and self.process.returncode is None:
            return
        pipes = [name for name in os.listdir("\\\\.\\pipe\\")
                 if name.startswith("codex-app-tools-")]
        if len(pipes) != 1:
            raise ConnectionError("Open one Codex desktop instance before submitting voice tasks")
        environment = os.environ.copy()
        environment["CODEX_APP_TOOLS_PIPE_PATH"] = "\\\\.\\pipe\\" + pipes[0]
        self.process = await asyncio.create_subprocess_exec(
            self.options["desktop_node"], self.options["desktop_server"],
            env=environment, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=8 * 1024 * 1024,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        await self.rpc("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "dania-desktop", "version": "1.0"}})
        self.process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        await self.process.stdin.drain()

    async def call(self, name, arguments):
        async with self.lock:
            try:
                async with asyncio.timeout(12):
                    await self.connect()
                    result = await self.rpc("tools/call", {
                        "name": name, "arguments": arguments,
                        "_meta": {"threadId": self.options["desktop_caller_thread"]}})
                    if result.get("isError"):
                        raise RuntimeError("Desktop tool rejected the request")
                    parts = [item["text"] for item in result.get("content", [])
                             if item.get("type") == "text"]
                    return json.loads("\n".join(parts))
            except BaseException:
                await self.close()
                # Never retry a create request: the desktop may already have accepted it.
                raise

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        self.process = None
