from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict

from .executor import validate_action

MAX_FRAME = 16 * 1024
MAX_PENDING = 4
MAX_IDS = 1024
ID_RETENTION_SECONDS = 30 * 60


def decode_frame(frame):
    if not isinstance(frame, str) or len(frame.encode("utf-8")) > MAX_FRAME:
        raise ValueError("Expected a text frame of at most 16 KiB")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result
    data = json.loads(frame, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid JSON number")))
    if not isinstance(data, dict):
        raise ValueError("Expected an object")
    return data


def result(call_id, ok, message, data=None):
    return {"type": "result", "id": call_id, "ok": ok, "message": message, "data": data or {}}


class Dispatcher:
    """A single worker; at most four accepted calls, including the running call.

    IDs survive reconnects for thirty minutes (longer than the maximum call TTL).
    A full dedupe cache rejects new work instead of evicting IDs that could replay.
    """

    def __init__(self, config, executor, wall_ms=None, monotonic=None):
        self.config, self.executor = config, executor
        self.wall_ms = wall_ms or (lambda: int(time.time() * 1000))
        self.monotonic = monotonic or time.monotonic
        self.ids = OrderedDict()
        self.queue = asyncio.Queue(maxsize=MAX_PENDING)
        self.pending = 0
        self.worker = None
        self.send = None

    async def start(self, send):
        self.send = send
        self.worker = asyncio.create_task(self._work())

    async def stop(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None
        while not self.queue.empty():
            call = self.queue.get_nowait()
            self._remember(call["id"], result(call["id"], False, "Disconnected before execution"))
            self.pending -= 1
            self.queue.task_done()
        self.send = None

    def _remember(self, call_id, response, signature=None):
        if signature is None and call_id in self.ids:
            signature = self.ids[call_id][2]
        self.ids[call_id] = (self.monotonic(), response, signature)

    async def accept(self, call):
        if not self.worker or self.worker.done():
            raise RuntimeError("Dispatcher is disconnected")
        call_id = call.get("id")
        if not isinstance(call_id, str) or not 1 <= len(call_id) <= 128:
            raise ValueError("Invalid call id")
        now = self.monotonic()
        for old, (seen, response, _) in list(self.ids.items()):
            if response is not None and now - seen >= ID_RETENTION_SECONDS:
                del self.ids[old]
        if call_id in self.ids:
            signature = hashlib.sha256(json.dumps(call, sort_keys=True).encode()).hexdigest()
            if signature != self.ids[call_id][2]:
                await self.send(result(call_id, False, "Call id was reused with different content"))
                return
            response = self.ids[call_id][1]
            if response is not None:
                await self.send(response)
            return
        if len(self.ids) >= MAX_IDS:
            # Never execute unrecorded work. Cache remains bounded.
            await self.send(result(call_id, False, "Call history is full; try later with a new id"))
            return
        try:
            if call.keys() != {"type", "id", "action", "args", "deadline_ms"} or call["type"] != "call":
                raise ValueError("Invalid call fields")
            deadline = call["deadline_ms"]
            if type(deadline) is not int or not 0 < deadline - self.wall_ms() <= 15000:
                raise ValueError("Call expired or deadline exceeds fifteen seconds")
            validate_action(self.config, call["action"], call["args"])
            if self.pending >= MAX_PENDING:
                raise ValueError("Computer is busy; call was not queued")
        except (ValueError, TypeError) as exc:
            response = result(call_id, False, str(exc))
            self._remember(call_id, response, hashlib.sha256(json.dumps(call, sort_keys=True).encode()).hexdigest())
            await self.send(response)
            return
        self._remember(call_id, None, hashlib.sha256(json.dumps(call, sort_keys=True).encode()).hexdigest())
        self.pending += 1
        call = dict(call)
        call["_expires_at"] = self.monotonic() + (call["deadline_ms"] - self.wall_ms()) / 1000
        self.queue.put_nowait(call)

    async def _work(self):
        while True:
            call = await self.queue.get()
            try:
                # Recheck after queue wait, immediately before the native executor.
                if call["deadline_ms"] <= self.wall_ms() or self.monotonic() >= call["_expires_at"]:
                    response = result(call["id"], False, "Call expired before execution")
                else:
                    def before_effect():
                        if (call["deadline_ms"] <= self.wall_ms() or
                                self.monotonic() >= call["_expires_at"]):
                            raise TimeoutError("Call expired before execution")
                    message, data = await self.executor.execute(call["action"], call["args"], before_effect)
                    response = result(call["id"], True, message, data)
            except TimeoutError:
                response = result(call["id"], False, "Call expired before execution")
            except asyncio.CancelledError:
                self._remember(call["id"], result(call["id"], False, "Disconnected during execution; not retried"))
                raise
            except Exception:
                # Do not leak paths, credentials, query text or OS exception contents to the cloud.
                response = result(call["id"], False, "Computer action failed")
            finally:
                self.pending -= 1
                self.queue.task_done()
            self._remember(call["id"], response)
            await self.send(response)
