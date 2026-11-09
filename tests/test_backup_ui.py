"""Renderer checks for SSH, redirected output, and diagnostic severity."""

import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backup_ui", ROOT / "system/backup_ui.py")
backup_ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup_ui)


class ReporterTests(unittest.TestCase):
    def test_redirected_plain_output_has_no_animation_and_keeps_diagnostics_in_log(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr):
            path = Path(directory) / "backup.log"
            ui = backup_ui.Reporter(plain=True)
            ui.attach(path)
            ui.command_line("Enabled services: example", "STDOUT")
            ui.command_line("\033[31mtar: secrets: Permission denied\033[0m", "STDERR")
            ui.composition([("Home Assistant + MQTT", "homeassistant-20261109-190714-611436+0300.tar.gz")])
            ui.error("Нет доступа", ["secrets"])
            ui.ok("Запуск → выполнен")
            ui.last_snapshot = 0
            ui.progress("Проверка", 9)
            ui.close()
            log = path.read_text()
        output = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn("Enabled services", output)
        self.assertIn("1. Home Assistant + MQTT", stdout.getvalue())
        self.assertNotIn("homeassistant-20261109-190714-611436+0300.tar.gz", output)
        self.assertIn("homeassistant-20261109-190714-611436+0300.tar.gz", log)
        self.assertIn("[ERROR] ОШИБКА", stderr.getvalue())
        self.assertIn("[OK] Запуск -> выполнен", stdout.getvalue())
        self.assertIn("[ERROR] STDERR: tar: secrets: Permission denied", log)
        self.assertIn("[DEBUG] STDOUT: Enabled services: example", log)
        self.assertNotIn("\r", output)
        self.assertNotIn("\033[", output + log)


if __name__ == "__main__":
    unittest.main()
