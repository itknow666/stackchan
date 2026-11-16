import asyncio
import dataclasses
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from dania_pc.client import Client, secure_connector
from dania_pc.config import Config, load_config
from dania_pc.executor import NativeExecutor, validate_action
from dania_pc.protocol import Dispatcher, MAX_IDS, decode_frame


def configuration(state_file=Path("unused")):
    return Config("wss://example.com/dania/pc/ws", "x" * 32, "desktop",
                  frozenset({"open_app", "open_project", "set_volume", "media", "search_web", "lock"}),
                  {"editor": {"name": "Editor", "executable": "C:/Editor/editor.exe", "args": []}},
                  {"robot": {"name": "Robot", "path": "C:/Robot"}}, True, state_file)


def call(call_id="a", **changes):
    value = {"type": "call", "id": call_id, "action": "lock", "args": {}, "deadline_ms": 11000}
    value.update(changes)
    return value


class FakeExecutor:
    def __init__(self):
        self.calls = []
        self.gate = None
        self.entered = asyncio.Event()

    async def execute(self, action, args, before_effect):
        self.entered.set()
        if self.gate:
            await self.gate.wait()
        before_effect()
        self.calls.append((action, args))
        return "Simulated", {"simulated": True}


class ValidationTests(unittest.TestCase):
    def test_config_and_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            data = json.loads((Path(__file__).parents[2] / "examples/pc-assistant.example.json").read_text())
            with self.assertRaises(ValueError):
                path.write_text(json.dumps(data))
                load_config(path)
            data["token"] = "a" * 32
            path.write_text(json.dumps(data))
            config = load_config(path)
            self.assertTrue(config.dry_run)
            self.assertEqual(config.state_file, path.resolve().parent / "private/status.json")
            self.assertEqual(config.hello()["capabilities"]["apps"], [])
            for endpoint in ("ws://example.com/dania/pc/ws", "wss://user:pw@example.com/dania/pc/ws",
                             "wss://example.com/other", "wss://example.com/dania/pc/ws?token=x"):
                data["endpoint"] = endpoint
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_arguments_are_strict(self):
        config = configuration()
        invalid = [("shell", {}), ("open_app", {"id": "cmd"}),
                   ("open_project", {"path": "C:/"}), ("lock", {"force": True}),
                   ("set_volume", {"value": True}), ("set_volume", {"value": 101}),
                   ("set_volume", {"value": 2.5}), ("media", {"command": "stop"}),
                   ("search_web", {"query": "  "}), ("search_web", {"query": "x\nrun"}),
                   ("search_web", {"query": "x" * 301})]
        for action, args in invalid:
            with self.subTest(action=action, args=args), self.assertRaises(ValueError):
                validate_action(config, action, args)
        for action, args in [("open_app", {"id": "editor"}), ("open_project", {"id": "robot"}),
                             ("set_volume", {"value": 0}), ("set_volume", {"value": 100}),
                             ("media", {"command": "next"}), ("search_web", {"query": "机器人"})]:
            validate_action(config, action, args)
        with self.assertRaises(ValueError):
            validate_action(dataclasses.replace(config, actions=frozenset()), "lock", {})

    def test_large_capability_hello_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            data = {"endpoint": "wss://example.com/dania/pc/ws", "token": "a" * 32,
                    "actions": ["open_project"], "projects": [
                        {"id": str(i), "name": "名字" * 50, "path": "C:/Project"} for i in range(64)]}
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "frame limit"):
                load_config(path)

    def test_decode_rejects_nontext_duplicate_nan_and_oversized(self):
        for frame in (b"{}", "[]", '{"id":"a","id":"b"}', '{"x":NaN}', '"' + "x" * 17000 + '"'):
            with self.subTest(frame=str(frame)[:40]), self.assertRaises(ValueError):
                decode_frame(frame)


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now, self.monotonic = 10000, 0.0
        self.executor = FakeExecutor()
        self.responses = []
        self.dispatcher = Dispatcher(configuration(), self.executor, lambda: self.now, lambda: self.monotonic)
        async def send(response):
            self.responses.append(response)
        self.send = send
        await self.dispatcher.start(send)

    async def asyncTearDown(self):
        await self.dispatcher.stop()

    async def settle(self):
        await asyncio.wait_for(self.dispatcher.queue.join(), 1)
        await asyncio.sleep(0)

    async def test_expired_future_and_extra_arguments_never_execute(self):
        for index, changes in enumerate(({"deadline_ms": 10000}, {"deadline_ms": 26000},
                                         {"deadline_ms": True}, {"args": {"extra": True}},
                                         {"extra": True})):
            await self.dispatcher.accept(call(str(index), **changes))
        self.assertEqual(len(self.responses), 5)
        self.assertTrue(all(not value["ok"] for value in self.responses))
        self.assertEqual(self.executor.calls, [])

    async def test_nonprintable_search_queries_never_reach_executor(self):
        invalid = ["query\x7f", "query\u202e", "query\u200b", "query\u00a0",
                   "query\u2028", "query\x00"]
        for index, query in enumerate(invalid):
            await self.dispatcher.accept(call(str(index), action="search_web", args={"query": query}))
        await self.settle()
        self.assertEqual(self.executor.calls, [])
        self.assertEqual(len(self.responses), len(invalid))
        self.assertTrue(all(not response["ok"] for response in self.responses))

        valid = ["普通中文", "English query", " 中文 English with ASCII spaces "]
        for index, query in enumerate(valid):
            await self.dispatcher.accept(call("valid-" + str(index), action="search_web",
                                              args={"query": query}))
            await self.settle()
        self.assertEqual(self.executor.calls,
                         [("search_web", {"query": query}) for query in valid])
        self.assertTrue(all(response["ok"] for response in self.responses[len(invalid):]))

    async def test_duplicate_running_and_completed_ids_do_not_replay(self):
        self.executor.gate = asyncio.Event()
        await self.dispatcher.accept(call())
        await self.executor.entered.wait()
        await self.dispatcher.accept(call())
        self.executor.gate.set()
        await self.settle()
        await self.dispatcher.accept(call())
        self.assertEqual(len(self.executor.calls), 1)
        self.assertEqual(len(self.responses), 2)
        self.assertEqual(self.responses[0], self.responses[1])

    async def test_recheck_deadline_immediately_before_effect(self):
        self.executor.gate = asyncio.Event()
        await self.dispatcher.accept(call())
        await self.executor.entered.wait()
        self.now = 12000
        self.executor.gate.set()
        await self.settle()
        self.assertFalse(self.responses[0]["ok"])
        self.assertEqual(self.executor.calls, [])

    async def test_duplicate_id_with_changed_payload_is_explicitly_rejected(self):
        await self.dispatcher.accept(call())
        await self.settle()
        await self.dispatcher.accept(call(action="media", args={"command": "next"}))
        self.assertEqual(len(self.executor.calls), 1)
        self.assertFalse(self.responses[-1]["ok"])
        self.assertIn("different content", self.responses[-1]["message"])

    async def test_monotonic_deadline_survives_clock_going_backwards(self):
        self.executor.gate = asyncio.Event()
        await self.dispatcher.accept(call())
        await self.executor.entered.wait()
        self.now = 9000
        self.monotonic = 2
        self.executor.gate.set()
        await self.settle()
        self.assertEqual(self.executor.calls, [])

    async def test_queue_is_bounded_and_waiting_calls_expire(self):
        self.executor.gate = asyncio.Event()
        await self.dispatcher.accept(call("0"))
        await self.executor.entered.wait()
        for index in range(1, 5):
            await self.dispatcher.accept(call(str(index)))
        self.assertEqual(self.dispatcher.pending, 4)
        self.assertFalse(self.responses[0]["ok"])
        self.now = 12000
        self.executor.gate.set()
        await self.settle()
        self.assertEqual(self.executor.calls, [])
        self.assertEqual(self.dispatcher.pending, 0)

    async def test_disconnect_cancels_and_drops_pending_work_without_replay(self):
        self.executor.gate = asyncio.Event()
        await self.dispatcher.accept(call("running"))
        await self.executor.entered.wait()
        await self.dispatcher.accept(call("queued"))
        await self.dispatcher.stop()
        self.executor.gate.set()
        await self.dispatcher.start(self.send)
        await self.dispatcher.accept(call("running"))
        await self.dispatcher.accept(call("queued"))
        self.assertEqual(self.executor.calls, [])
        self.assertEqual(self.dispatcher.pending, 0)
        self.assertTrue(all(not response["ok"] for response in self.responses))

    async def test_synchronous_invalidation_revokes_running_executor_gate(self):
        self.executor.gate = asyncio.Event()
        await self.dispatcher.accept(call("running"))
        await self.executor.entered.wait()
        await self.dispatcher.accept(call("queued"))
        self.dispatcher.invalidate()
        self.assertEqual(self.dispatcher.pending, 1)
        self.executor.gate.set()
        await self.settle()
        self.assertEqual(self.executor.calls, [])
        self.assertEqual(self.dispatcher.pending, 0)
        self.assertEqual(self.responses, [])
        await self.dispatcher.stop()
        await self.dispatcher.start(self.send)
        await self.dispatcher.accept(call("running"))
        await self.dispatcher.accept(call("queued"))
        self.assertEqual(len(self.responses), 2)
        self.assertTrue(all(not response["ok"] for response in self.responses))

    async def test_dedupe_capacity_does_not_evict_live_ids(self):
        for index in range(MAX_IDS):
            self.dispatcher._remember(str(index), {"ok": False})
        await self.dispatcher.accept(call("new"))
        self.assertEqual(len(self.dispatcher.ids), MAX_IDS)
        self.assertEqual(self.executor.calls, [])
        self.monotonic = 1801
        await self.dispatcher.accept(call("newer"))
        await self.settle()
        self.assertEqual(len(self.executor.calls), 1)

    async def test_dry_run_never_calls_windows(self):
        message, data = await NativeExecutor(configuration()).execute("lock", {})
        self.assertIn("Simulated", message)
        self.assertEqual(data, {"simulated": True})


class LoopbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_buffered_calls_are_dropped_at_normal_and_exceptional_receive_close(self):
        class Socket:
            def __init__(self, calls, exceptional=False):
                self.calls = iter(calls)
                self.exceptional = exceptional
                self.closed = False
                self.responses = []
            async def send(self, frame):
                value = json.loads(frame)
                if value["type"] == "result":
                    self.responses.append(value)
            async def recv(self):
                return '{"type":"ready","version":1}'
            def __aiter__(self):
                return self
            async def __anext__(self):
                try:
                    return json.dumps(next(self.calls))
                except StopIteration:
                    self.closed = True
                    if self.exceptional:
                        raise ConnectionError("Transport closed abnormally")
                    raise StopAsyncIteration
        for exceptional in (False, True):
            with self.subTest(exceptional=exceptional), tempfile.TemporaryDirectory() as directory:
                executor = FakeExecutor()
                client = Client(configuration(Path(directory) / "status.json"), executor)
                calls = [call(str(i), deadline_ms=int(time.time() * 1000) + 5000) for i in range(4)]
                socket = Socket(calls, exceptional)
                if exceptional:
                    with self.assertRaises(ConnectionError):
                        await client.session(socket)
                else:
                    await client.session(socket)
                self.assertTrue(socket.closed)
                self.assertEqual(executor.calls, [])
                self.assertEqual(client.dispatcher.pending, 0)
                self.assertEqual(len(client.dispatcher.ids), 4)
                # The same calls on a new live session return cached failures only.
                reconnected = Socket(calls)
                await client.session(reconnected)
                self.assertEqual(executor.calls, [])
                self.assertEqual(len(reconnected.responses), 4)
                self.assertTrue(all(not response["ok"] for response in reconnected.responses))
                self.assertFalse(json.loads(client.config.state_file.read_text())["connected"])

    async def test_outgoing_client_auth_limits_cancellation_and_private_status(self):
        class Connection:
            async def __aenter__(self):
                raise OSError("Do not log secrets")
            async def __aexit__(self, *args):
                return False
        options = []
        called = asyncio.Event()
        def connector(endpoint, **kwargs):
            options.append((endpoint, kwargs))
            called.set()
            return Connection()
        with tempfile.TemporaryDirectory() as directory:
            config = configuration(Path(directory) / "status.json")
            client = Client(config, FakeExecutor(), connector)
            task = asyncio.create_task(client.run())
            await called.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(options[0][1]["additional_headers"], {"Authorization": "Bearer " + config.token})
            self.assertEqual(options[0][1]["max_size"], 16384)
            self.assertEqual(options[0][1]["max_queue"], 4)
            self.assertIsNone(options[0][1]["proxy"])
            self.assertFalse(json.loads(config.state_file.read_text())["connected"])

    async def test_authentication_redirects_are_never_followed(self):
        connector = secure_connector()("wss://example.com/dania/pc/ws")
        error = RuntimeError("redirect")
        self.assertIs(connector.process_redirect(error), error)

    async def test_actual_websocket_handshake_call_result_and_disconnect(self):
        from websockets.asyncio.client import connect
        from websockets.asyncio.server import serve
        with tempfile.TemporaryDirectory() as directory:
            config = configuration(Path(directory) / "status.json")
            executor = FakeExecutor()
            client = Client(config, executor)
            received = []
            async def server(ws):
                received.append(decode_frame(await ws.recv()))
                await ws.send(json.dumps({"type": "ready", "version": 1}))
                action = call(deadline_ms=int(time.time() * 1000) + 5000)
                await ws.send(json.dumps(action))
                received.append(decode_frame(await ws.recv()))
            async with serve(server, "127.0.0.1", 0) as listener:
                port = listener.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as ws:
                    await asyncio.wait_for(client.session(ws), 3)
            self.assertEqual(received[0], config.hello())
            self.assertTrue(received[1]["ok"])
            self.assertEqual(received[1]["data"], {"simulated": True})
            self.assertEqual(len(executor.calls), 1)
            state = json.loads(config.state_file.read_text())
            self.assertFalse(state["connected"])
            self.assertNotIn(config.token, config.state_file.read_text())
            self.assertEqual(state['capabilities'],config.hello()['capabilities'])
            self.assertNotIn('executable',config.state_file.read_text())
            self.assertNotIn('C:/Robot',config.state_file.read_text())
            self.assertEqual(state["pending"], 0)

    async def test_invalid_ready_rejected_before_execution(self):
        class Socket:
            async def send(self, _):
                pass
            async def recv(self):
                return '{"type":"ready","version":true}'
        with tempfile.TemporaryDirectory() as directory:
            client = Client(configuration(Path(directory) / "status.json"), FakeExecutor())
            with self.assertRaises(ValueError):
                await client.session(Socket())
            self.assertFalse(client.connected)


class StartupTests(unittest.TestCase):
    def test_startup_helper_writes_only_user_startup_and_no_token(self):
        import startup
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "配置.json"
            config.write_text('{"token":"private-test-value"}')
            pythonw = root / "pythonw.exe"
            pythonw.touch()
            with patch.dict("os.environ", {"APPDATA": directory}), \
                    patch("sys.executable", str(root / "python.exe")), \
                    patch("sys.argv", ["startup.py", "install", "--config", str(config)]):
                startup.main()
            entry = root / "Microsoft/Windows/Start Menu/Programs/Startup/DaniaPcAssistant.vbs"
            text = entry.read_text(encoding="utf-16")
            self.assertIn(str(config.resolve()), text)
            self.assertNotIn("private-test-value", text)
            with patch.dict("os.environ", {"APPDATA": directory}), patch("sys.argv", ["startup.py", "remove"]):
                startup.main()
            self.assertFalse(entry.exists())


if __name__ == "__main__":
    unittest.main()
