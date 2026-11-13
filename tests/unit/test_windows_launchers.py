"""以替身 CLI 驗證真 CMD 路由及退出碼，不連線或開啟 UART。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "win32", "需要 Windows CMD")
class WindowsLauncherTests(unittest.TestCase):
    def test_launchers_wait_and_preserve_cli_exit_code(self):
        repo = Path(__file__).resolve().parents[2]
        if not (repo / ".venv/Scripts/python.exe").is_file():
            self.skipTest("需要專案虛擬環境")
        with tempfile.TemporaryDirectory(prefix="uart launcher space ") as temporary:
            root = Path(temporary)
            package = root / "uart_gateway"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "cli.py").write_text(
                "import json, os, sys\n"
                "print(json.dumps(sys.argv[1:]), flush=True)\n"
                "sys.exit(int(os.environ['UART_TEST_EXIT']))\n", encoding="utf-8")
            for launcher, command in (("run-uart.cmd", "menu"), ("run-remote.cmd", "remote")):
                for code in (0, 7, 130):
                    with self.subTest(launcher=launcher, code=code):
                        env = dict(os.environ, PYTHONPATH=str(root), UART_TEST_EXIT=str(code))
                        result = subprocess.run(
                            ["cmd.exe", "/d", "/c", str(repo / launcher)], cwd=root, env=env,
                            capture_output=True, timeout=20)
                        self.assertEqual(result.returncode, code, result.stderr.decode(errors="replace"))
                        self.assertEqual(json.loads(result.stdout), [command, "--pause-on-error"])
