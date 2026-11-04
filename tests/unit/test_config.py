import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch

from uart_gateway.server.app import load


class ConfigTests(unittest.TestCase):
    def test_example_loads_without_hardware_and_does_not_expose_secret(self):
        token = secrets.token_urlsafe(32)
        with patch.dict(os.environ, {"UART_GATEWAY_TOKEN": token}):
            gateway, host, port = load(Path(__file__).parents[2] / "examples/gateway.fake.toml")
        self.assertEqual((host, port), ("127.0.0.1", 18081))
        self.assertNotIn(token, repr(gateway.principals))
        self.assertEqual(gateway.channels["ch_lab_1"].state, "closed")

    def test_missing_secret_and_unsupported_config_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.toml"
            path.write_text('channels=[]\n[auth]\nprincipals=[]\n[recording]\nenabled=true\n')
            with self.assertRaises(ValueError):
                load(path)
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            load(Path(__file__).parents[2] / "examples/gateway.fake.toml")

    def test_real_com3_profile_loads_readonly_without_opening_hardware(self):
        path = Path(__file__).parents[2] / "examples/gateway.com3.toml"
        with patch.dict(os.environ, {"UART_GATEWAY_TOKEN": secrets.token_urlsafe(32)}), \
                patch("uart_gateway.serial_backend._serial_module") as module:
            gateway, host, port = load(path, fake=False)
            module.assert_not_called()
        channel = gateway.channels["ch_uart_1"]
        self.assertEqual(channel.port.selector.port, "COM3")
        self.assertFalse(channel.config.serial.dtr)
        self.assertEqual(channel.config.serial.baudrate, 115200)
        self.assertEqual(gateway.principals[0].grants["ch_uart_1"], frozenset({"read"}))
        self.assertEqual(channel.state, "closed")

    def test_fake_cannot_silently_use_real_profile(self):
        path = Path(__file__).parents[2] / "examples/gateway.com3.toml"
        with self.assertRaises(ValueError):
            load(path, fake=True)
