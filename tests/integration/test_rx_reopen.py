"""單通道重開的真 TCP／SDK／CLI 與模擬 Driver；不是 Win11 USB 驗證。"""

import asyncio
import secrets
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from uart_gateway.client import Client
from uart_gateway.core.models import ChannelConfig
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.protocol.errors import ProtocolError, RemoteError
from uart_gateway.remote_workbench import query_channels, reopen_channel
from uart_gateway.serial_backend import SerialBackendError
from uart_gateway.server.auth import Principal
from uart_gateway.server.tcp import Gateway
from tests.fakes import eventually


class ReopenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tokens = {name: secrets.token_urlsafe(32) for name in ("manager", "reader", "writer")}
        self.devices = [FakeSerial(echo=False), FakeSerial(echo=False)]
        self.gateway = Gateway([(ChannelConfig(cid, cid), port) for cid, port in zip(("a", "b"), self.devices)], (
            Principal("manager", self.tokens["manager"], {"a": frozenset({"read", "configure"})}),
            Principal("reader", self.tokens["reader"], {cid: frozenset({"read"}) for cid in ("a", "b")}),
            Principal("writer", self.tokens["writer"], {"a": frozenset({"read", "tx"})})))
        self.port = await self.gateway.start(port=0)
        self.clients = []

    async def connect(self, role="manager"):
        client = await Client.connect("127.0.0.1", self.port, self.tokens[role])
        self.clients.append(client)
        return client

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.gateway.close()

    async def lose(self):
        self.devices[0].close()
        await eventually(lambda: self.gateway.channels["a"].state == "lost")

    async def test_reopen_preserves_instance_peer_subscription_and_read_only(self):
        manager, reader = await self.connect(), await self.connect("reader")
        first = await reader.get_channel("a")
        peer = await reader.get_channel("b")
        subscription = await reader.subscribe("b")
        await self.lose()
        lost = await manager.get_channel("a")
        result = await manager.reopen_rx(lost)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["binding"]["status"], "unconfirmed")
        self.assertEqual(int(result["binding"]["revision"]), int(lost["binding"]["revision"]) + 1)
        self.assertNotEqual(result["stream_epoch"], first["stream_epoch"])
        self.assertEqual(result["rx"]["next_offset"], "0")
        self.assertEqual(result["tx"]["state"], "idle")
        self.assertEqual(manager.instance, self.gateway.store.instance)
        self.assertEqual(peer["stream_epoch"], (await reader.get_channel("b"))["stream_epoch"])
        self.devices[1].inject(b"peer continues")
        event = await asyncio.wait_for(reader.next_event(), 1)
        self.assertEqual(event["data"]["subscription_id"], subscription["subscription_id"])
        self.assertEqual(event["event"], "rx")
        await manager.subscribe("a")
        self.devices[0].inject(b"new stream")
        event = await asyncio.wait_for(manager.next_event(), 1)
        self.assertEqual(event["stream_epoch"], result["stream_epoch"])
        with self.assertRaises(RemoteError) as error:
            await manager.acquire(result)
        self.assertEqual(error.exception.code, "PERMISSION_DENIED")
        self.assertTrue(all(d.write_count == 0 for d in self.devices))

    async def test_acl_and_stale_context_rejected_without_hardware_operation(self):
        manager = await self.connect()
        await self.lose()
        lost = await manager.get_channel("a")
        with patch.object(self.devices[0], "open", wraps=self.devices[0].open) as opened:
            for role in ("reader", "writer"):
                client = await self.connect(role)
                with self.assertRaises(RemoteError) as error:
                    await client.reopen_rx(lost)
                self.assertEqual(error.exception.code, "PERMISSION_DENIED")
            for name, value, code in (("gateway_instance_id", "other", "INSTANCE_MISMATCH"),
                                      ("binding_revision", "0", "STALE_BINDING"),
                                      ("stream_epoch", "old", "EPOCH_MISMATCH")):
                expected = manager.expected(lost) | {name: value}
                with self.assertRaises(RemoteError) as error:
                    await manager.transport.request("channel.reopen_rx", {"channel_id": "a", "expected": expected})
                self.assertEqual(error.exception.code, code)
            peer = await (await self.connect("reader")).get_channel("b")
            with self.assertRaises(RemoteError) as error:
                await manager.reopen_rx(peer)
            self.assertEqual(error.exception.code, "PERMISSION_DENIED")
            opened.assert_not_called()
        await manager.reopen_rx(lost)
        with self.assertRaises(RemoteError) as error:
            await manager.reopen_rx(lost)
        self.assertEqual(error.exception.code, "STALE_BINDING")

    async def test_missing_busy_ambiguous_failure_can_be_queried_and_retried_manually(self):
        client = await self.connect()
        await self.lose()
        for code in ("DEVICE_MISSING", "PORT_BUSY", "AMBIGUOUS_ADAPTER"):
            old = await client.get_channel("a")
            with patch.object(self.devices[0], "open", side_effect=SerialBackendError(code, "模擬失敗")):
                with self.assertRaises(RemoteError) as error:
                    await client.reopen_rx(old)
            self.assertEqual(error.exception.code, code)
            current = await client.get_channel("a")
            self.assertEqual(current["state"], "lost" if code == "DEVICE_MISSING" else "faulted")
            self.assertIsNone(current["stream_epoch"])
            self.assertEqual(current["binding"]["status"], "unconfirmed")
            self.assertEqual(int(current["binding"]["revision"]), int(old["binding"]["revision"]) + 1)
        self.assertEqual((await client.reopen_rx(current))["state"], "ready")
        self.assertEqual(self.devices[0].write_count, 0)

    async def test_logging_without_client_or_recorder_contains_lifecycle_but_not_rx_or_token(self):
        payload = b"private-uart-payload"
        with self.assertLogs("uart_gateway.server.tcp", level="INFO") as logs:
            self.devices[0].inject(payload)
            await eventually(lambda: self.gateway.channels["a"].offset == len(payload))
            await self.lose()
            client = await self.connect()
            await client.reopen_rx(await client.get_channel("a"))
            await self.gateway.channels["a"].stop()
        text = "\n".join(logs.output)
        for state in ("裝置遺失", "DEVICE_MISSING", "人工 RX 重開請求", "開啟中", "就緒", "已關閉", "修訂", "世代"):
            self.assertIn(state, text)
        self.assertNotIn(payload.decode(), text)
        for token in self.tokens.values():
            self.assertNotIn(token, text)
        self.assertFalse(self.gateway.recorders)

    async def test_old_server_capability_and_uncertain_response_never_retried(self):
        client = await self.connect()
        await self.lose()
        lost = await client.get_channel("a")
        caps = client.hello["capabilities"]
        client.hello["capabilities"] = []
        with patch.object(client.transport, "request") as request:
            with self.assertRaises(ProtocolError) as error:
                await client.reopen_rx(lost)
            self.assertEqual(error.exception.code, "UNKNOWN_METHOD")
            request.assert_not_called()
        client.hello["capabilities"] = caps
        with patch.object(client.transport, "request", side_effect=TimeoutError) as request:
            with self.assertRaisesRegex(ConnectionError, "結果未確認"):
                await client.reopen_rx(lost)
            request.assert_awaited_once()

    async def test_workbench_action_requires_capability_grant_and_matching_snapshot(self):
        connect = lambda: self.connect("manager")
        instance, entries = await query_channels(connect)
        self.assertTrue(entries[0]["reopen_rx_available"])
        _, read_entries = await query_channels(lambda: self.connect("reader"))
        self.assertTrue(all(not c.get("reopen_rx_available") for c in read_entries))
        await self.lose()
        with patch.object(self.devices[0], "open", wraps=self.devices[0].open) as opened:
            with self.assertRaisesRegex(ValueError, "Gateway 已改變"):
                await reopen_channel(connect, entries[0], instance + "old")
            with self.assertRaisesRegex(ValueError, "修訂或資料流已改變"):
                await reopen_channel(connect, entries[0], instance)
            opened.assert_not_called()
        _, entries = await query_channels(connect)
        result = await reopen_channel(connect, entries[0], instance)
        self.assertEqual(result["state"], "ready")
        self.assertTrue(all(c.transport.reader_task.done() for c in self.clients))

    async def test_real_cli_reopen_success_ready_rejection_and_read_token_denial(self):
        async def command(role):
            with tempfile.TemporaryDirectory() as directory:
                token_file = Path(directory) / "auth.token"
                token_file.write_text(self.tokens[role])
                process = await asyncio.create_subprocess_exec(sys.executable, "-m", "uart_gateway.cli",
                    "--port", str(self.port), "--token-file", str(token_file), "reopen", "a",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                out, err = await asyncio.wait_for(process.communicate(), 5)
                return process.returncode, out.decode(), err.decode()

        await self.lose()
        code, out, err = await command("reader")
        self.assertEqual(code, 1)
        self.assertIn("PERMISSION_DENIED", err)
        code, out, err = await command("manager")
        self.assertEqual(code, 0)
        self.assertIn('"unconfirmed"', out)
        self.assertIn("單通道 RX 已重新開啟", err)
        code, out, err = await command("manager")
        self.assertEqual(code, 1)
        self.assertIn("INVALID_STATE", err)
        self.assertEqual(self.devices[0].write_count, 0)
