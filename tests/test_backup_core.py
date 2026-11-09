"""Generic discovery, sudo ownership, and Docker's native terminal output."""

import errno
import fcntl
import os
from pathlib import Path
import pty
import runpy
import selectors
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class BackupCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(sys, "path", [str(ROOT / "system"), *sys.path]):
            cls.module = runpy.run_path(str(ROOT / "system/backup.py"), run_name="backup_test")

    def test_discovery_after_adding_and_renaming_arbitrary_plugins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main, plugins = root / "homeassistant", root / "plugins"
            (main / "config").mkdir(parents=True)
            (main / "mosquitto").mkdir()
            plugins.mkdir()
            for folder in (main, plugins):
                (folder / "docker-compose.yml").touch()
            names = {"arbitrary-addon", "another.addon", "documentation-only"}
            for name in names:
                (plugins / name).mkdir()
            discover = self.module["plan"]
            tasks = discover(main, plugins, "STAMP")
            self.assertEqual({t.label for t in tasks[1:-1]}, names)
            (plugins / "arbitrary-addon").rename(plugins / "renamed-addon")
            (plugins / "new-addon").mkdir()
            names = names - {"arbitrary-addon"} | {"renamed-addon", "new-addon"}
            tasks = discover(main, plugins, "STAMP")
            self.assertEqual({t.label for t in tasks[1:-1]}, names)
            self.assertEqual({t.name for t in tasks[1:-1]}, {f"plugin-{name}-STAMP.tar.gz" for name in names})

    def test_owner_uses_sudo_ids_and_direct_root_stays_root(self):
        access = self.module["BackupAccess"]
        with patch("os.geteuid", return_value=0), patch("os.getuid", return_value=0), \
                patch("os.getgid", return_value=0), patch.dict(os.environ, {}, clear=True):
            self.assertEqual((access.caller().uid, access.caller().gid), (0, 0))
            with patch.dict(os.environ, {"SUDO_UID": "1234", "SUDO_GID": "5678"}):
                self.assertEqual((access.caller().uid, access.caller().gid), (1234, 5678))
            for env in ({"SUDO_UID": "1234"}, {"SUDO_UID": "-1", "SUDO_GID": "1234"},
                        {"SUDO_UID": "4294967295", "SUDO_GID": "1234"}):
                with self.subTest(env=env), patch.dict(os.environ, env):
                    with self.assertRaises(self.module["BackupError"]):
                        access.caller()

    def test_grant_sets_owner_on_every_artifact_without_following_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            access = self.module["BackupAccess"](os.getuid(), os.getgid())
            output = access.create_output(root / "new/backups", "STAMP")
            for name in ("archive.tar.gz", "backup.log", "result.json", "SUCCESS"):
                (output / name).write_text("artifact")
            real_chown = os.fchown
            with patch("os.fchown", wraps=real_chown) as chown:
                access.grant(output)
            self.assertEqual(chown.call_count, 7)  # Four files and three directories.
            for call in chown.call_args_list:
                self.assertEqual(call.args[1:], (os.getuid(), os.getgid()))
            for path in (root / "new", output.parent, output, *output.iterdir()):
                self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
            outside = root / "outside"
            outside.write_text("untouched")
            outside.chmod(0o644)
            (output / "link").symlink_to(outside)
            with self.assertRaises(self.module["BackupError"]):
                access.grant(output)
            self.assertEqual(outside.stat().st_mode & 0o777, 0o644)

    def test_native_output_keeps_docker_tty_animation_and_plain_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "backup.log"
            helper = root / "terminal.py"
            child = (
                "import os, sys; "
                "print('TTY=' + str(sys.stdout.isatty()) + '/' + str(sys.stderr.isatty())); "
                "print('ANSI=' + os.environ.get('COMPOSE_ANSI', 'auto')); "
                "os.write(2, b'\\x1b[32mContainer arbitrary Stopping\\x1b[0m\\r'); "
                "os.write(2, b'\\x1b[2KContainer arbitrary Stopped\\n')"
            )
            helper.write_text(
                "import sys\n" + f"sys.path.insert(0, {str(ROOT / 'system')!r})\n"
                "from backup import run\nfrom backup_ui import Reporter\n"
                f"ui = Reporter()\nfrom pathlib import Path\nui.attach(Path({str(log)!r}))\n"
                f"run([sys.executable, '-c', {child!r}], ui, 'Lifecycle', native=True)\nui.close()\n"
            )
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
            env = dict(os.environ, TERM="xterm-256color", PYTHONDONTWRITEBYTECODE="1")
            env.pop("COMPOSE_ANSI", None)
            process = subprocess.Popen([sys.executable, str(helper)], stdout=slave, stderr=slave, env=env)
            os.close(slave)
            chunks = []
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(master, selectors.EVENT_READ)
                    deadline = time.monotonic() + 10
                    while selector.get_map() and time.monotonic() < deadline:
                        for key, _events in selector.select(timeout=0.2):
                            try:
                                chunk = os.read(key.fd, 65536)
                            except OSError as error:
                                if error.errno != errno.EIO:
                                    raise
                                chunk = b""
                            if not chunk:
                                selector.unregister(key.fd)
                            else:
                                chunks.append(chunk)
                    self.assertFalse(selector.get_map(), "native output did not finish")
                self.assertEqual(process.wait(timeout=3), 0)
            finally:
                os.close(master)
                if process.poll() is None:
                    process.kill()
                    process.wait()
            output = b"".join(chunks).decode()
            self.assertIn("TTY=True/True", output)
            self.assertIn("ANSI=auto", output)
            self.assertIn("\x1b[32mContainer arbitrary Stopping", output)
            self.assertIn("\x1b[2KContainer arbitrary Stopped", output)
            self.assertNotIn("Сервисы:", output)
            self.assertNotIn("\x1b[", log.read_text())
            self.assertIn("Container arbitrary Stopped", log.read_text())


if __name__ == "__main__":
    unittest.main()
