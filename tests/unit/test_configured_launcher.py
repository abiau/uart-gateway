"""整合啟動的設定邊界與秘密參照；不開啟實體裝置。"""

import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from uart_gateway.configured_launcher import config_text, prepare_config
from uart_gateway.server.app import assemble


class ConfiguredTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "gateway.toml"
        self.config = tomllib.loads(Path("examples/gateway.dual.rx-reopen.toml").read_text())
        self.env = patch.dict(os.environ, {"UART_GATEWAY_TOKEN": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def prepare(self, **options):
        self.path.write_text(config_text(self.config), encoding="utf-8")
        before = self.path.read_bytes()
        result = prepare_config(self.path, **options)
        self.assertEqual(before, self.path.read_bytes())
        return result

    def test_roundtrip_recording_override_and_no_secret_in_config_or_repr(self):
        self.config["channels"][0]["target_note"] = '中文 "引號" 與 \\路徑'
        plan = self.prepare(record_directory=Path(self.temp.name) / "錄製")
        self.assertNotIn(plan.token, plan.config)
        self.assertNotIn(plan.token, repr(plan))
        self.assertTrue(plan.temporary_token)
        parsed = tomllib.loads(plan.config)
        self.assertEqual(parsed["channels"], self.config["channels"])
        gateway, host, port = assemble(parsed, fake=False, resolve_secret=lambda _: plan.token)
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, plan.port)
        self.assertEqual(len(gateway.channels), 2)
        self.assertEqual(gateway.principals[0].token, plan.token)
        self.assertTrue(parsed["recording"]["enabled"])

    def test_file_reference_is_reused_and_not_printed(self):
        token = "t" * 43
        secret = Path(self.temp.name) / "secret"
        secret.write_text(token)
        self.config["auth"]["principals"][0]["token_secret_ref"] = "file:" + str(secret)
        plan = self.prepare()
        self.assertEqual(plan.token, token)
        self.assertFalse(plan.temporary_token)
        self.assertNotIn(token, plan.config)

    def test_reject_tx_multi_identity_unsafe_serial_and_bad_types(self):
        original = config_text(self.config)
        for change in (
            lambda c: c["auth"]["principals"][0]["permissions"].append("tx"),
            lambda c: c["auth"]["principals"].append(dict(c["auth"]["principals"][0])),
            lambda c: c["channels"][0]["serial"].update(dtr=True),
            lambda c: c.update(auth="invalid"),
            lambda c: c.update(recording="invalid"),
        ):
            self.config = tomllib.loads(original)
            change(self.config)
            with self.assertRaises(ValueError):
                self.prepare()

    def test_explicit_disable_removes_recording_limits(self):
        self.config["recording"] = {"enabled": True, "directory": "old", "queue_bytes": 262144}
        plan = self.prepare(no_record=True)
        self.assertIsNone(plan.recording)
        self.assertEqual(tomllib.loads(plan.config)["recording"], {"enabled": False})
