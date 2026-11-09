"""Renderer checks for SSH, redirected output, and diagnostic severity."""

import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backup_ui", ROOT / "system/backup_ui.py")
backup_ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup_ui)


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ReporterTests(unittest.TestCase):
    def test_narrow_tty_progress_is_bounded_errors_keep_full_paths(self):
        stdout, stderr = Terminal(), Terminal()
        with patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr), \
                patch.object(backup_ui.shutil, "get_terminal_size", return_value=os.terminal_size((36, 24))):
            ui = backup_ui.Reporter(no_color=True)
            ui.progress("Архивирование очень длинного имени компонента", 12, size=1024)
            rendered = stdout.getvalue().split("\r")[-1]
            self.assertLessEqual(len(rendered), 35)
            ui.error("Недоступен файл", ["/a/very/long/path/that/must/not/be/truncated/secrets.yaml"])
            self.assertEqual(ui.active_width, 0)
            self.assertIn("/a/very/long/path/that/must/not/be/truncated/secrets.yaml", stderr.getvalue())
        self.assertIn("\r", stdout.getvalue())
        self.assertNotIn("\033[", stdout.getvalue() + stderr.getvalue())

    def test_redirected_plain_output_has_no_animation_and_keeps_diagnostics_in_log(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr):
            path = Path(directory) / "backup.log"
            ui = backup_ui.Reporter(plain=True)
            ui.attach(path)
            ui.command_line("Enabled services: example", "STDOUT")
            ui.command_line("\033[31mtar: secrets: Permission denied\033[0m", "STDERR")
            ui.error("Нет доступа", ["secrets"])
            ui.ok("Запуск → выполнен")
            ui.last_snapshot = 0
            ui.progress("Проверка", 9)
            ui.close()
            log = path.read_text()
        output = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn("Enabled services", output)
        self.assertIn("[ERROR] ОШИБКА", stderr.getvalue())
        self.assertIn("[OK] Запуск -> выполнен", stdout.getvalue())
        self.assertIn("[ERROR] STDERR: tar: secrets: Permission denied", log)
        self.assertIn("[DEBUG] STDOUT: Enabled services: example", log)
        self.assertNotIn("\r", output)
        self.assertNotIn("\033[", output + log)


if __name__ == "__main__":
    unittest.main()
