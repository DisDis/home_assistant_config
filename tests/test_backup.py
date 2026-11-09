"""Backup integration tests: real tar/Compose, simulated Docker daemon only."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
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
        for name in ("backup.sh", "backup.py", "ha.sh", "plugins.sh", "plugins.py"):
            shutil.copy(ROOT / "system" / name, self.system)
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

    def invoke(self, answer="yes\n", **env):
        return subprocess.run(["bash", str(self.system / "backup.sh")], cwd="/tmp",
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
        expected = {"homeassistant.tar.gz", "plugins-config.tar.gz"}
        expected.update(f"plugin-{name}.tar.gz" for name in (
            "matter-server", "hass-configurator", "zigbee2mqtt", "future-plugin", "yandex-smart-home"))
        self.assertEqual({p.name for p in output.glob("*.tar.gz")}, expected)
        self.assertTrue((output / "SUCCESS").is_file())
        self.assertFalse((output / "FAILED").exists())
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        for path in output.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with tarfile.open(output / "homeassistant.tar.gz") as archive:
            self.assertEqual(archive.extractfile("homeassistant/config/.storage/test").read(), b"HA integration state")
            self.assertEqual(archive.extractfile("homeassistant/mosquitto/data/mosquitto.db").read(), b"MQTT state")
            self.assertIn("homeassistant/.env", archive.getnames())
            self.assertNotIn("homeassistant/backups/old.tar.gz", archive.getnames())
            self.assertNotIn("homeassistant/.ha-backup.lock", archive.getnames())
        with tarfile.open(output / "plugin-future-plugin.tar.gz") as archive:
            self.assertEqual(archive.extractfile("plugins/future-plugin/data/state").read(), b"future-plugin saved state")
        with tarfile.open(output / "plugins-config.tar.gz") as archive:
            self.assertEqual(set(archive.getnames()), {"plugins/.env", "plugins/docker-compose.yml"})
        for line in (output / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)
        self.assertNotIn("\033[", (output / "backup.log").read_text())
        mutations = self.mutations()
        self.assertEqual(mutations[0][0], "stop")
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
        self.assertIn("ERROR", result.stderr)

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

    def test_verification_failure_does_not_claim_success_services_are_running(self):
        result = self.invoke(FAIL_VERIFY="1")
        self.assertNotEqual(result.returncode, 0)
        self.assert_running()
        self.assertFalse((self.output_dir() / "SUCCESS").exists())
        self.assertTrue((self.output_dir() / "FAILED").is_file())

    def test_signal_recovery_and_lock_across_different_destinations(self):
        process = subprocess.Popen(["bash", str(self.system / "backup.sh")],
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
        second = subprocess.run([
            "bash", str(self.system / "backup.sh"), str(self.main), str(self.plugins),
            str(self.root / "other-backups")], input="yes\n", text=True, capture_output=True,
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


if __name__ == "__main__":
    unittest.main()
