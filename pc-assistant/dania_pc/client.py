from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import time

from .executor import NativeExecutor
from .protocol import Dispatcher, MAX_FRAME, decode_frame

LOG = logging.getLogger("dania_pc")


def secure_connector():
    from websockets.asyncio.client import connect

    class NoRedirectConnect(connect):
        def process_redirect(self, exc):
            # Never forward the bearer token to a redirected host or endpoint.
            return exc

    return NoRedirectConnect


class Client:
    def __init__(self, config, executor=None, connector=None):
        self.config = config
        self.dispatcher = Dispatcher(config, executor or NativeExecutor(config))
        self.connector = connector
        self.connected = False
        self.last_result = None

    def write_state(self):
        # No token, endpoint, paths, query, or action arguments in status files.
        state = {"version": 1, "pc_id": self.config.pc_id, "connected": self.connected,
                 "dry_run": self.config.dry_run, "pending": self.dispatcher.pending,
                 "capabilities": self.config.hello()["capabilities"],
                 "updated_at_ms": int(time.time() * 1000), "last_result": self.last_result}
        manager = getattr(self.dispatcher.executor, "codex_tasks", None)
        if manager: state["codex_tasks"] = manager.projection()
        path = self.config.state_file
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)

    async def session(self, ws):
        await ws.send(json.dumps(self.config.hello()))
        ready = decode_frame(await asyncio.wait_for(ws.recv(), timeout=10))
        if ready != {"type": "ready", "version": 1} or type(ready.get("version")) is not int:
            raise ValueError("Invalid server ready frame")
        self.connected = True
        self.write_state()

        async def send(response):
            try:
                await ws.send(json.dumps(response, ensure_ascii=False))
            except BaseException:
                self.dispatcher.invalidate()
                self.connected = False
                raise
            self.last_result = {"ok": response["ok"], "at_ms": int(time.time() * 1000)}
            self.write_state()

        async def receive():
            try:
                async for frame in ws:
                    await self.dispatcher.accept(decode_frame(frame))
            finally:
                # This must happen without an await: the worker may be next in the
                # ready queue before session() observes that the reader has ended.
                self.dispatcher.invalidate()
                self.connected = False

        async def state_loop():
            while True:
                await asyncio.sleep(2)
                self.write_state()

        reader = state_task = None
        try:
            await self.dispatcher.start(send)
            reader = asyncio.create_task(receive())
            state_task = asyncio.create_task(state_loop())
            done, _ = await asyncio.wait({reader, state_task, self.dispatcher.worker},
                                         return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            self.dispatcher.invalidate()
            self.connected = False
            for task in (reader, state_task):
                if task:
                    task.cancel()
            await asyncio.gather(*(t for t in (reader, state_task) if t), return_exceptions=True)
            await self.dispatcher.stop()
            self.write_state()

    async def run(self):
        if self.connector is None:
            self.connector = secure_connector()
        delay = 1
        self.write_state()
        try:
            while True:
                started = time.monotonic()
                try:
                    # Default TLS verification is retained. No inbound PC listener.
                    async with self.connector(
                            self.config.endpoint,
                            additional_headers={"Authorization": "Bearer " + self.config.token},
                            max_size=MAX_FRAME, max_queue=4, write_limit=MAX_FRAME,
                            open_timeout=10, close_timeout=3, ping_interval=20, ping_timeout=20,
                            compression=None, proxy=None) as ws:
                        await self.session(ws)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOG.warning("Computer connection unavailable; reconnecting with backoff")
                if time.monotonic() - started >= 30:
                    delay = 1
                await asyncio.sleep(delay + random.uniform(0, delay / 4))
                delay = min(delay * 2, 30)
        finally:
            manager = getattr(self.dispatcher.executor, "codex_tasks", None)
            if manager: await manager.close()
            await self.dispatcher.stop()
            self.connected = False
            with contextlib.suppress(OSError):
                self.write_state()
