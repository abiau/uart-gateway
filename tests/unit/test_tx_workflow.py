"""TX Payload、私有收據與明確角色；不操作實體 UART。"""

import json
import io
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uart_gateway.auth_setup import export_credential, import_credential, initialize
from uart_gateway.client import resolve_connection
from uart_gateway.client.tx_receipts import TxJournal, TxReceipt
from uart_gateway.client.tx_workflow import encode_payload
from uart_gateway.protected_files import read_private
from uart_gateway.server.app import load


class PayloadTests(unittest.TestCase):
    def test_literal_text_unicode_and_explicit_newline_are_exact(self):
        for mode, suffix in (("none", b""), ("lf", b"\n"), ("cr", b"\r"), ("crlf", b"\r\n")):
            self.assertEqual(encode_payload("開機q\\n", mode), "開機q\\n".encode() + suffix)
        self.assertEqual(encode_payload("00 ff 0D 0a", encoding="hex"), b"\0\xff\r\n")
        self.assertEqual(encode_payload("", "lf"), b"\n")

    def test_hidden_controls_bad_hex_and_byte_limits_are_rejected(self):
        for text in ("a\nb", "a\rb", "a\tb", "\x1b[31m", "\0", ""):
            with self.assertRaises(ValueError):
                encode_payload(text)
        for text in ("f", "hello", "0x01"):
            with self.assertRaises(ValueError):
                encode_payload(text, encoding="hex")
        self.assertEqual(len(encode_payload("a" * 4096)), 4096)
        for text, ending in (("a" * 4096, "lf"), ("開" * 1366, "none")):
            with self.assertRaises(ValueError):
                encode_payload(text, ending)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = TxJournal(Path(self.temp.name) / "receipts")

    def test_independent_receipts_roundtrip_and_never_save_payload(self):
        receipt = TxReceipt("a" * 32, "uart_1", "instance", "operator")
        self.journal.save(receipt, create=True)
        self.assertEqual(self.journal.list(), [receipt])
        with self.assertRaises(ValueError):
            self.journal.save(receipt, create=True)
        receipt.result.update(state="written_to_driver", status_origin="record", submitted_bytes=4, accepted_bytes=4, io_started=True, reason_code=None)
        self.journal.save(receipt)
        saved = json.loads(read_private(self.journal.path(receipt), 4096))
        self.assertEqual(set(saved), {"tx_id", "channel_id", "instance", "principal", "created", "result", "last_query", "execution"})
        self.assertEqual(self.journal.list()[0].result["state"], "written_to_driver")

    def test_malformed_or_traversal_receipt_is_not_silently_skipped(self):
        with self.assertRaises(ValueError):
            self.journal.save(TxReceipt("../escape", "a", "g", "p"), create=True)
        self.journal.directory.mkdir(exist_ok=True)
        p = self.journal.directory / ("tx-" + "a" * 32 + ".json")
        p.write_text('{"unexpected":"value"}')
        p.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "損壞"):
            self.journal.list()

    def test_unresolved_receipts_are_bounded_and_not_evicted(self):
        for number in range(128):
            self.journal.save(TxReceipt(f"{number:032x}", "a", "g", "p"), create=True)
        with self.assertRaisesRegex(ValueError, "未結案"):
            self.journal.save(TxReceipt("f" * 32, "a", "g", "p"), create=True)
        self.assertEqual(len(self.journal.list()), 128)

    def test_known_results_are_pruned_but_success_cannot_be_fabricated_by_bad_counts(self):
        for number in range(70):
            receipt = TxReceipt(f"{number:032x}", "a", "g", "p")
            receipt.result.update(state="written_to_driver", status_origin="record", submitted_bytes=4,
                                  accepted_bytes=4, io_started=True, reason_code=None)
            self.journal.save(receipt, create=True)
        self.assertLessEqual(len(self.journal.list()), 64)
        receipt.result["accepted_bytes"] = 2
        with self.assertRaisesRegex(ValueError, "不能判定成功"):
            self.journal.save(receipt)

    def test_another_workbench_cannot_regress_a_known_terminal_result(self):
        receipt = TxReceipt("b" * 32, "a", "g", "p")
        self.journal.save(receipt, create=True)
        stale = deepcopy(receipt)
        receipt.result.update(state="written_to_driver", status_origin="record", submitted_bytes=1,
                              accepted_bytes=1, io_started=True, reason_code=None)
        self.journal.save(receipt)
        self.journal.save(stale)
        self.assertEqual(self.journal.list()[0].result["state"], "written_to_driver")


class TxCredentialTests(unittest.TestCase):
    def test_tx_is_explicit_separate_and_exported_without_privilege_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = Path("examples/gateway.dual.rx-reopen.toml")
            config = initialize(source, name="tx-test", directory=root / "authority", enable_tx=True, tx_channels=["ch_uart_1"])
            gateway, _, _ = load(config, fake=False)
            self.assertEqual([p.id for p in gateway.principals], ["remote_reader", "remote_manager", "remote_tx"])
            self.assertEqual(gateway.principals[2].grants, {"ch_uart_1": frozenset({"read", "configure", "tx"})})
            self.assertEqual(len({p.token for p in gateway.principals}), 3)
            self.assertTrue(all("tx" not in p for g in gateway.principals[:2] for p in g.grants.values()))
            bundle = export_credential(name="tx-test", access="tx", directory=root / "authority")
            import_credential(read_private(bundle, 16384), directory=root / "client")
            self.assertEqual(resolve_connection(access="tx", directory=root / "client").access, "tx")
            with self.assertRaisesRegex(ValueError, "未配置"):
                resolve_connection(directory=root / "client")
            self.assertNotIn(gateway.principals[2].token, config.read_text())

    def test_bad_tx_channel_list_never_creates_deployment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for options in ({"tx_channels": ["ch_uart_1"]}, {"enable_tx": True, "tx_channels": ["missing"]},
                            {"enable_tx": True, "tx_channels": ["ch_uart_1", "ch_uart_1"]}):
                with self.assertRaises(ValueError):
                    initialize(Path("examples/gateway.com3.toml"), directory=root, **options)
                self.assertFalse((root / "connections/default").exists())


class TxEntryTests(unittest.TestCase):
    def test_workbench_x_selects_tx_role_and_preserves_sdk_profile_directory(self):
        from uart_gateway.cli import main
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("sys.argv", ["uart", "--connections-dir", str(root), "menu"]), \
                    patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                    patch("builtins.input", side_effect=["x", "q"]), patch("sys.stderr", io.StringIO()), \
                    patch("uart_gateway.cli.client_connection", return_value=Mock()) as source, \
                    patch("uart_gateway.tui.run_tui", new_callable=AsyncMock, return_value=0) as tui:
                with self.assertRaises(SystemExit) as result:
                    main()
            self.assertEqual(result.exception.code, 0)
            args = source.call_args.args[0]
            self.assertEqual((args.command, args.access, args.connections_dir), ("tx", "tx", root))
            self.assertFalse(args.port_explicit)
            self.assertIs(tui.call_args.kwargs["settings"], source.return_value)
