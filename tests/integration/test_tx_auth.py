"""明確 TX 憑證的真 Gateway／SDK；原讀取與管理不升級。"""

import tempfile
from pathlib import Path
import unittest

from uart_gateway.auth_setup import export_credential, import_credential, initialize
from uart_gateway.client import Client
from uart_gateway.client.tx_receipts import TxJournal
from uart_gateway.client.tx_workflow import TxWorkflow
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.protected_files import read_private, write_private
from uart_gateway.server.app import load


class TxAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = initialize(Path("examples/gateway.com3.toml"), directory=self.root / "authority", enable_tx=True)
        self.gateway, _, _ = load(config, fake=False)
        self.device = FakeSerial(echo=False)
        self.gateway.channels["ch_uart_1"].port = self.device
        self.port = await self.gateway.start(port=0)
        for role in ("read", "manage", "tx"):
            package = export_credential(access=role, directory=self.root / "authority")
            import_credential(read_private(package, 16384), port=self.port, directory=self.root / "client")

    async def asyncTearDown(self):
        await self.gateway.close()

    async def test_tx_explicitly_connects_and_default_reader_remains_read_only(self):
        async with await Client.connect_auto(directory=self.root / "client") as reader:
            self.assertEqual(reader.auth["grants"][0]["permissions"], ["read"])
        async def connect():
            return await Client.connect_auto(directory=self.root / "client", access="tx")
        workflow = TxWorkflow(connect, TxJournal(self.root / "receipts"))
        async with await connect() as client:
            snapshot = await client.get_channel("ch_uart_1")
            instance = client.instance
        target = await workflow.acknowledge(await workflow.prepare(snapshot, instance))
        result = await workflow.send(target, b"explicit-tx")
        self.assertEqual(result.result["state"], "written_to_driver")
        async with await Client.connect_auto(directory=self.root / "client", access="manage") as manager:
            self.assertNotIn("tx", manager.auth["grants"][0]["permissions"])
        self.assertEqual(self.device.write_count, 1)

    async def test_mislabelled_reader_or_tx_role_is_closed_without_io(self):
        directory = self.root / "client/connections/default"
        reader = read_private(directory / "read.secret", 1024)
        tx = read_private(directory / "tx.secret", 1024)
        write_private(directory / "tx.secret", reader, replace=True)
        with self.assertRaisesRegex(ValueError, "權限.*不符"):
            await Client.connect_auto(directory=self.root / "client", access="tx")
        write_private(directory / "read.secret", tx, replace=True)
        with self.assertRaisesRegex(ValueError, "權限.*不符"):
            await Client.connect_auto(directory=self.root / "client")
        self.assertEqual(self.device.write_count, 0)
