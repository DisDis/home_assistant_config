"""Backup integration tests: real tar/Compose, simulated Docker daemon only."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI is required")
class BackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ha-backup-tests-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "installation with spaces"
        self.root.mkdir()
        self.main = self.root / "homeassistant"
        self.plugins = self.root / "plugins"
        self.system = self.root / "system"
        for directory in (self.main, self.plugins, self.system):
            directory.mkdir()
        for name in ("backup.sh", "backup.py", "backup_ui.py", "ha.sh", "plugins.sh", "plugins.py"):
            shutil.copy(ROOT / "system" / name, self.system)
        # Only the euid policy is simulated. File opens retain the real OS permissions.
        self.runner = self.root / "run_backup.py"
        self.runner.write_text('''import errno, os, pathlib, runpy, shutil, sys
from contextlib import ExitStack
from unittest.mock import patch
sys.dont_write_bytecode = True
script = pathlib.Path(__file__).parent / "system/backup.py"
sys.path.insert(0, str(script.parent))
sys.argv = [str(script), *sys.argv[1:]]
with ExitStack() as patches:
    patches.enter_context(patch("os.geteuid", return_value=int(os.environ.get("TEST_EUID", "0"))))
    if "TEST_FREE_BYTES" in os.environ:
        usage = shutil.disk_usage(script.parent)
        patches.enter_context(patch("shutil.disk_usage", return_value=usage._replace(free=int(os.environ["TEST_FREE_BYTES"]))))
    if os.environ.get("TEST_REPORT_FAILURE"):
        module = runpy.run_path(str(script), run_name="backup_under_test")
        original = module["atomic_json"]
        failed = False
        def save(path, report):
            global failed
            if report["stop_attempted"] and not failed:
                failed = True
                raise OSError(errno.ENOSPC, "simulated result write failure", str(path))
            original(path, report)
        module["main"].__globals__["atomic_json"] = save
        sys.exit(module["main"]())
    else:
        runpy.run_path(str(script), run_name="__main__")
''')
        shutil.copy(ROOT / "homeassistant/docker-compose.yml", self.main)
        shutil.copy(ROOT / "plugins/docker-compose.yml", self.plugins)
        (self.plugins / ".env").write_text("COMPOSE_PROFILES=matter-server\n")
        (self.main / ".env").write_text("HA_BACKEND_NETWORK=test-backend\n")
        for relative in ("config/.storage", "mosquitto/data", "mosquitto/config", "mosquitto/log"):
            (self.main / relative).mkdir(parents=True, exist_ok=True)
        (self.main / "config/.storage/test").write_text("HA integration state")
        (self.main / "config/home-assistant_v2.db").write_bytes(b"database snapshot")
        (self.main / "mosquitto/data/mosquitto.db").write_text("MQTT state")
        (self.main / "backups").mkdir()
        (self.main / "backups/old.tar.gz").write_text("must not be included")
        for name in ("matter-server", "hass-configurator", "zigbee2mqtt", "future-plugin"):
            data = self.plugins / name / "data"
            data.mkdir(parents=True)
            (data / "state").write_text(name + " saved state")
        (self.plugins / "yandex-smart-home").mkdir()
        (self.plugins / "yandex-smart-home/README.md").write_text("HACS instructions")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.state = self.root / "state.json"
        self.state.write_text("{}")
        self.events = self.root / "events.jsonl"
        self.marker = self.root / "tar-started"
        self.docker_log = self.root / "docker.jsonl"
        (self.bin / "docker").write_text('''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if os.environ.get("FAIL_MAIN_STOP") and args[0] == "compose" and "stop" in args:
    print("simulated partial stop failure", file=sys.stderr)
    sys.exit(12)
if os.environ.get("FAIL_RESTART") and os.path.exists(os.environ["TAR_MARKER"]):
    os.environ["FAKE_FAIL_UP"] = "1"
os.execv(sys.executable, [sys.executable, os.environ["DOCKER_STUB"], *args])
''')
        (self.bin / "tar").write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys, time
args = sys.argv[1:]
if "--create" in args:
    state = json.loads(pathlib.Path(os.environ["FAKE_DOCKER_STATE"]).read_text())
    if any(c["State"]["Running"] for c in state.values()):
        print("archive attempted while services were running", file=sys.stderr)
        sys.exit(91)
    pathlib.Path(os.environ["TAR_MARKER"]).touch()
    with open(os.environ["TAR_EVENTS"], "a") as events:
        events.write(json.dumps(args) + "\\n")
    if os.environ.get("FAIL_ARCHIVE"):
        pathlib.Path(args[args.index("--file") + 1]).write_text("incomplete")
        print("simulated archive failure: no space left", file=sys.stderr)
        sys.exit(13)
    if os.environ.get("WAIT_ARCHIVE"):
        time.sleep(60)
elif "--list" in args:
    state = json.loads(pathlib.Path(os.environ["FAKE_DOCKER_STATE"]).read_text())
    if not all(c["State"]["Running"] for c in state.values()):
        print("verification attempted before restart", file=sys.stderr)
        sys.exit(92)
    if os.environ.get("FAIL_VERIFY"):
        print("simulated corrupt archive", file=sys.stderr)
        sys.exit(14)
os.execv(os.environ["REAL_TAR"], [os.environ["REAL_TAR"], *args])
''')
        for executable in self.bin.iterdir():
            executable.chmod(0o755)
        prefixes = ("COMPOSE_", "HC_", "HA_", "PLUGINS_", "ZIGBEE", "HASS_CONFIGURATOR", "MATTER_")
        self.env = {key: value for key, value in os.environ.items() if not key.startswith(prefixes)}
        self.env.update(
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            REAL_DOCKER=shutil.which("docker"), REAL_TAR=shutil.which("tar"),
            DOCKER_STUB=str(ROOT / "tests/fake_docker.py"),
            FAKE_DOCKER_LOG=str(self.docker_log), FAKE_DOCKER_STATE=str(self.state),
            TAR_MARKER=str(self.marker), TAR_EVENTS=str(self.events),
        )
        started = subprocess.run(["bash", str(self.system / "ha.sh"), "start"],
                                 env=self.env, capture_output=True, text=True)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.docker_log.write_text("")

    def command(self, *args):
        return [sys.executable, str(self.runner), *map(str, args)]

    def invoke(self, answer="yes\n", *, args=(), **env):
        return subprocess.run(self.command(*args), cwd="/tmp",
                              input=answer, text=True, capture_output=True,
                              env=dict(self.env, **env), timeout=30)

    def output_dir(self):
        return next((self.root / "backups").iterdir())

    def assert_running(self):
        self.assertTrue(all(c["State"]["Running"] for c in json.loads(self.state.read_text()).values()))

    def mutations(self):
        commands = [json.loads(line) for line in self.docker_log.read_text().splitlines()]
        return [c for c in commands if c[0] in ("stop", "rm")
                or c[0] == "compose" and ("up" in c or "pull" in c or "stop" in c)]

    def test_success_separate_archives_hidden_state_permissions_and_checksums(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_running()
        output = self.output_dir()
        timestamp = output.name
        self.assertRegex(timestamp, r"^\d{8}-\d{6}-\d{6}[+-]\d{4}$")
        expected = {f"homeassistant-{timestamp}.tar.gz", f"plugins-config-{timestamp}.tar.gz"}
        expected.update(f"plugin-{name}-{timestamp}.tar.gz" for name in (
            "matter-server", "hass-configurator", "zigbee2mqtt", "future-plugin", "yandex-smart-home"))
        self.assertEqual({p.name for p in output.glob("*.tar.gz")}, expected)
        for name in expected:
            self.assertNotIn(name, result.stdout)
            self.assertIn(name, (output / "backup.log").read_text())
        self.assertIn("1. Home Assistant + MQTT", result.stdout)
        plugin_names = {path.name for path in self.plugins.iterdir() if path.is_dir()}
        for name in plugin_names:
            self.assertRegex(result.stdout, rf"(?m)^\s+\d+\. {re.escape(name)}$")
        self.assertNotIn("Яндекс", result.stdout + result.stderr)
        self.assertTrue((output / "SUCCESS").is_file())
        self.assertFalse((output / "FAILED").exists())
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        for path in output.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with tarfile.open(output / f"homeassistant-{timestamp}.tar.gz") as archive:
            self.assertEqual(archive.extractfile("homeassistant/config/.storage/test").read(), b"HA integration state")
            self.assertEqual(archive.extractfile("homeassistant/mosquitto/data/mosquitto.db").read(), b"MQTT state")
            self.assertIn("homeassistant/.env", archive.getnames())
            self.assertNotIn("homeassistant/backups/old.tar.gz", archive.getnames())
            self.assertNotIn("homeassistant/.ha-backup.lock", archive.getnames())
        with tarfile.open(output / f"plugin-future-plugin-{timestamp}.tar.gz") as archive:
            self.assertEqual(archive.extractfile("plugins/future-plugin/data/state").read(), b"future-plugin saved state")
        with tarfile.open(output / f"plugins-config-{timestamp}.tar.gz") as archive:
            self.assertEqual(set(archive.getnames()), {"plugins/.env", "plugins/docker-compose.yml"})
        checksum_lines = (output / "SHA256SUMS").read_text().splitlines()
        self.assertEqual({line.split("  ", 1)[1] for line in checksum_lines}, expected)
        for line in checksum_lines:
            digest, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)
        self.assertNotIn("\033[", (output / "backup.log").read_text())
        self.assertNotIn("\033[", result.stdout + result.stderr)
        self.assertNotIn("\r", result.stdout)
        self.assertNotIn("Enabled services:", result.stdout)
        self.assertIn("Enabled services:", (output / "backup.log").read_text())
        report = json.loads((output / "result.json").read_text())
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["backup_status"], "verified")
        self.assertEqual(report["services_status"], "running")
        self.assertTrue(report["stop_attempted"])
        self.assertTrue(report["restart_attempted"])
        self.assertGreater(report["downtime_seconds"], 0)
        self.assertFalse(report["downtime_ongoing"])
        self.assertEqual({item["name"] for item in report["archives"]}, expected)
        self.assertEqual({item["component"] for item in report["archives"]},
                         plugin_names | {"Home Assistant + MQTT", "Общие настройки плагинов"})
        for item in report["archives"]:
            self.assertEqual(item["status"], "verified")
            self.assertEqual(item["sha256"], hashlib.sha256((output / item["name"]).read_bytes()).hexdigest())
        mutations = self.mutations()
        self.assertEqual(mutations[0][:3], ["stop", "-t", "60"])
        self.assertIn("stop", mutations[1])
        self.assertIn(str(self.main), mutations[1])
        self.assertIn(str(self.main), mutations[2])
        self.assertIn("up", mutations[2])
        self.assertIn(str(self.plugins), mutations[3])
        self.assertFalse(any("pull" in command for command in mutations))

    def test_refusal_empty_input_eof_and_other_words_never_stop_services(self):
        for answer in ("no\n", "\n", "", "да\n", "YES\n"):
            with self.subTest(answer=answer):
                result = self.invoke(answer)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(self.mutations(), [])
                self.assertFalse((self.root / "backups").exists())
                self.assertFalse((self.main / ".ha-backup.lock").exists())
                self.assert_running()

    def test_archive_failure_restarts_services_and_removes_partial_file(self):
        result = self.invoke(FAIL_ARCHIVE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assert_running()
        output = self.output_dir()
        self.assertTrue((output / "FAILED").is_file())
        self.assertFalse((output / "SUCCESS").exists())
        self.assertEqual(list(output.glob("*.part")), [])
        self.assertIn("no space left", (output / "backup.log").read_text())
        self.assertIn("ОШИБКА", result.stderr)
        report = json.loads((output / "result.json").read_text())
        self.assertEqual(report["backup_status"], "failed")
        self.assertEqual(report["services_status"], "running")
        self.assertEqual(report["errors"][0]["phase"], "archive")
        self.assertTrue(any("no space left" in detail for detail in report["errors"][0]["details"]))

    def test_partial_stop_failure_still_attempts_restart_without_archiving(self):
        result = self.invoke(FAIL_MAIN_STOP="1")
        self.assertNotEqual(result.returncode, 0)
        self.assert_running()
        self.assertFalse(self.marker.exists())
        self.assertTrue((self.output_dir() / "FAILED").is_file())

    def test_invalid_configuration_does_not_stop_or_start_services(self):
        (self.main / "docker-compose.yml").write_text("services: [invalid]\n")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])
        self.assert_running()

    def test_restart_failure_reports_manual_command_and_no_success(self):
        result = self.invoke(FAIL_RESTART="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Не удалось запустить систему", result.stderr)
        self.assertIn("ha.sh' start", result.stderr)
        self.assertFalse((self.output_dir() / "SUCCESS").exists())
        self.assertTrue((self.output_dir() / "FAILED").is_file())
        report = json.loads((self.output_dir() / "result.json").read_text())
        self.assertEqual(report["backup_status"], "created")
        self.assertEqual(report["services_status"], "start_failed")
        self.assertTrue(report["downtime_ongoing"])

    def test_verification_failure_does_not_claim_success_services_are_running(self):
        result = self.invoke(FAIL_VERIFY="1")
        self.assertNotEqual(result.returncode, 0)
        self.assert_running()
        self.assertFalse((self.output_dir() / "SUCCESS").exists())
        self.assertTrue((self.output_dir() / "FAILED").is_file())
        report = json.loads((self.output_dir() / "result.json").read_text())
        self.assertEqual(report["backup_status"], "failed")
        self.assertEqual(report["services_status"], "running")

    def test_signal_recovery_and_lock_across_different_destinations(self):
        process = subprocess.Popen(self.command(),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=dict(self.env, WAIT_ARCHIVE="1"))
        self.addCleanup(lambda: process.poll() is None and process.kill())
        process.stdin.write("yes\n")
        process.stdin.flush()
        deadline = time.monotonic() + 10
        while not self.marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.marker.exists(), "first backup never reached archive step")
        mutations = self.mutations()
        second = subprocess.run(self.command(self.main, self.plugins, self.root / "other-backups"),
            input="yes\n", text=True, capture_output=True,
            env=self.env, timeout=10)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("уже выполняется", second.stderr)
        self.assertEqual(self.mutations(), mutations)
        self.assertFalse((self.root / "other-backups").exists())
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 143, stdout + stderr)
        self.assert_running()
        self.assertTrue((self.output_dir() / "FAILED").is_file())
        self.assertFalse((self.output_dir() / "SUCCESS").exists())
        self.assertEqual(list(self.output_dir().glob("*.part")), [])
        report = json.loads((self.output_dir() / "result.json").read_text())
        self.assertEqual(report["exit_code"], 143)
        self.assertEqual(report["services_status"], "running")

    def test_check_never_prompts_stops_archives_or_leaves_files(self):
        before = set(self.root.rglob("*"))
        result = self.invoke("", args=("--check",))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("введите yes", result.stdout)
        self.assertIn("Проверка: ПРОЙДЕНА", result.stdout)
        self.assertIn("Сервисы:  НЕ ИЗМЕНЯЛИСЬ", result.stdout)
        self.assertEqual(self.mutations(), [])
        self.assertEqual(set(self.root.rglob("*")), before)
        self.assertFalse(self.marker.exists())
        self.assert_running()

    def test_non_root_fails_before_prompt_and_docker_access(self):
        result = self.invoke(TEST_EUID="1000")
        self.assertEqual(result.returncode, 1)
        self.assertIn("sudo bash", result.stderr)
        self.assertNotIn("введите yes", result.stdout)
        self.assertEqual(self.docker_log.read_text(), "")
        self.assertFalse((self.root / "backups").exists())
        self.assert_running()

    @unittest.skipIf(os.geteuid() == 0, "Real Unix access-denied checks require an unprivileged test process")
    def test_unreadable_file_and_directory_fail_before_stopping(self):
        file = self.main / "config/.storage/test"
        directory = self.plugins / "matter-server/data"
        file.chmod(0)
        directory.chmod(0)
        self.addCleanup(file.chmod, 0o600)
        self.addCleanup(directory.chmod, 0o700)
        result = self.invoke()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("введите yes", result.stdout)
        self.assertIn("Не удалось прочитать 2", result.stderr)
        self.assertEqual(self.mutations(), [])
        self.assertFalse(self.marker.exists())
        self.assert_running()
        report = json.loads((self.output_dir() / "result.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["backup_status"], "not_started")
        self.assertEqual(report["services_status"], "unchanged")
        self.assertFalse(report["stop_attempted"])
        details = report["errors"][0]["details"]
        self.assertTrue(any(str(file) in detail for detail in details))
        self.assertTrue(any(str(directory) in detail for detail in details))

    @unittest.skipIf(os.geteuid() == 0, "Real Unix access-denied checks require an unprivileged test process")
    def test_unwritable_destination_fails_before_stop_and_without_archives(self):
        destination = self.root / "backups"
        destination.mkdir(mode=0o700)
        destination.chmod(0o500)
        self.addCleanup(destination.chmod, 0o700)
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertIn("недоступен для записи", result.stderr)
        self.assertEqual(self.mutations(), [])
        self.assertEqual(list(destination.iterdir()), [])
        self.assert_running()

    def test_verbose_plain_check_shows_commands_without_terminal_controls(self):
        result = self.invoke("", args=("--check", "--verbose", "--plain"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("STDOUT: Enabled services:", result.stdout)
        self.assertIn("[OK]", result.stdout)
        self.assertNotIn("\033[", result.stdout + result.stderr)
        self.assertNotIn("\r", result.stdout)
        self.assertFalse((self.root / "backups").exists())

    def test_no_space_fails_before_stop_and_saves_failed_report(self):
        result = self.invoke(TEST_FREE_BYTES="0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("свободного места", result.stderr)
        self.assertEqual(self.mutations(), [])
        self.assertFalse(self.marker.exists())
        report = json.loads((self.output_dir() / "result.json").read_text())
        self.assertFalse(report["stop_attempted"])
        self.assertEqual(report["services_status"], "unchanged")
        self.assertEqual(report["status"], "failed")
        self.assert_running()

    def test_report_write_failure_after_stop_still_restarts_services(self):
        result = self.invoke(TEST_REPORT_FAILURE="1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assert_running()
        output = self.output_dir()
        self.assertTrue((output / "FAILED").exists())
        self.assertFalse((output / "SUCCESS").exists())
        report = json.loads((output / "result.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["services_status"], "running")
        self.assertTrue(report["restart_attempted"])
        self.assertIn("Недостаточно свободного места", result.stderr)


if __name__ == "__main__":
    unittest.main()
