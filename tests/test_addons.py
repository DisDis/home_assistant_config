"""Profile and lifecycle regression tests. No daemon, images or hardware required."""

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
ADDON_NAMES = ("zigbee2mqtt", "hass-configurator", "matter-server")


@unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI is required")
class AddonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ha-compose-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addons = self.root / "addons"
        self.addons.mkdir()
        shutil.copy(ROOT / "addons/docker-compose.yml", self.addons)
        self.main = self.root / "homeassistant"
        self.main.mkdir()
        self.system = self.root / "system"
        self.system.mkdir(parents=True)
        for name in ("ha.sh", "addons.py", "addons.sh", "autostart.sh"):
            shutil.copy(ROOT / "system" / name, self.system)
        shutil.copy(ROOT / "homeassistant/docker-compose.yml", self.main)
        (self.main / "config").mkdir()
        for service in ADDON_NAMES:
            data = self.addons / service / "data"
            data.mkdir(parents=True)
            (data / "sentinel").write_text("existing state")
        for tool in ("portainer", "uptime-kuma"):
            directory = self.root / "tools" / tool
            directory.mkdir(parents=True)
            shutil.copy(ROOT / "tools" / tool / "docker-compose.yml", directory)
        secret_dir = self.root / "tools/portainer/secrets"
        secret_dir.mkdir()
        (secret_dir / "admin-password").write_text("test-only-placeholder")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        shutil.copy(ROOT / "tests/fake_docker.py", self.bin / "docker")
        (self.bin / "docker").chmod(0o755)
        (self.bin / "systemctl").write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_SYSTEMCTL_LOG"\n')
        (self.bin / "systemctl").chmod(0o755)
        self.log = self.root / "docker.jsonl"
        self.state = self.root / "state.json"
        self.state.write_text("{}")
        prefixes = ("COMPOSE_", "HC_", "HA_", "ADDONS_", "ZIGBEE", "HASS_CONFIGURATOR", "MATTER_")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(prefixes)}
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        REAL_DOCKER=shutil.which("docker"), FAKE_DOCKER_LOG=str(self.log),
                        FAKE_DOCKER_STATE=str(self.state), FAKE_SYSTEMCTL_LOG=str(self.root / "systemctl.log"))
        self.select(())

    def select(self, profiles, extra=""):
        (self.addons / ".env").write_text("COMPOSE_PROFILES=" + ",".join(profiles) + "\n" + extra)

    def command(self, args, *, success=True, env=None, cwd=None):
        result = subprocess.run(args, env=env or self.env, cwd=cwd or self.root,
                                capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def addon(self, action, **kwargs):
        return self.command(["bash", str(self.system / "addons.sh"), action, str(self.addons)], **kwargs)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def mutations(self):
        return [c for c in self.commands() if c[0] in ("stop", "rm")
                or c[0] == "compose" and ("up" in c or "pull" in c or "stop" in c)]

    def managed(self):
        return {c["Config"]["Labels"]["com.docker.compose.service"]
                for c in json.loads(self.state.read_text()).values()
                if c["Config"]["Labels"]["com.docker.compose.project"] == "ha-addons"}

    def config(self, all_profiles=False):
        args = ["docker", "compose", "--project-directory", str(self.addons), "--env-file", str(self.addons / ".env"),
                "-f", str(self.addons / "docker-compose.yml")]
        if all_profiles:
            args += ["--profile", "*"]
        return json.loads(self.command(args + ["config", "--format", "json"]).stdout)

    def test_arbitrary_addon_and_group_profile_need_no_script_changes(self):
        compose = self.addons / "docker-compose.yml"
        addition = '''
  arbitrary-service:
    profiles: [extra]
    image: registry.example.org/vendor/addon:1.2.3
    restart: unless-stopped
    volumes:
      - ./arbitrary/data:/data
  companion:
    profiles: [extra]
    image: registry.example.org/vendor/companion:1.2.3
    restart: unless-stopped
'''
        compose.write_text(compose.read_text().replace("\nnetworks:\n", addition + "\nnetworks:\n"))
        self.select(("extra",))
        self.addon("apply")
        self.assertEqual(self.managed(), {"arbitrary-service", "companion"})
        self.select(())
        self.addon("apply")
        self.assertEqual(self.managed(), set())

    def test_application_settings_are_not_validated_by_helper(self):
        self.select(ADDON_NAMES, "HC_PASSWORD=''\nZIGBEE_ADAPTER_PATH=/no/such/device\nHA_DIR=/missing/main\nMATTER_PRIMARY_INTERFACE=missing-interface\n")
        self.addon("validate")
        self.assertFalse(self.mutations())

    def test_data_mounts_are_separate_and_editor_shares_live_ha_config(self):
        self.select((), "ADDONS_BIND_IP=192.0.2.10\n")
        config = self.config(all_profiles=True)
        self.assertEqual(config["name"], "ha-addons")
        services = config["services"]
        for service, target in (("zigbee2mqtt", "/app/data"), ("hass-configurator", "/config"), ("matter-server", "/data")):
            source = next(v["source"] for v in services[service]["volumes"] if v["target"] == target)
            self.assertEqual(Path(source), self.addons / service / "data")
        shared = next(v for v in services["hass-configurator"]["volumes"] if v["target"] == "/hass-config")
        self.assertEqual(Path(shared["source"]), self.main / "config")
        self.assertFalse(shared.get("bind", {}).get("create_host_path", False))
        for service in services.values():
            self.assertEqual(service["logging"]["options"], {"max-size": "10m", "max-file": "3"})
            if service.get("network_mode") != "host":
                self.assertEqual(service["ports"][0]["host_ip"], "192.0.2.10")
            self.assertNotIn(":latest", service["image"])

    def test_matter_is_optional_and_keeps_host_network_and_hardware_access(self):
        main = json.loads(self.command([
            "docker", "compose", "--project-directory", str(self.main),
            "--env-file", os.devnull, "config", "--format", "json"]).stdout)
        self.assertEqual(set(main["services"]), {"homeassistant", "mosquitto"})
        integrations = next(v for v in main["services"]["homeassistant"]["volumes"]
                            if v["target"] == "/integrations")
        self.assertEqual(Path(integrations["source"]), self.root / "integrations")
        matter = self.config(all_profiles=True)["services"]["matter-server"]
        self.assertEqual(matter["profiles"], ["matter-server"])
        self.assertEqual(matter["network_mode"], "host")
        self.assertTrue(matter["privileged"])
        mounts = {v["target"]: v["source"] for v in matter["volumes"]}
        self.assertEqual(mounts["/run/dbus"], "/run/dbus")
        self.assertEqual(mounts["/dev/bus/usb"], "/dev/bus/usb")
        self.assertEqual(mounts["/data"], str(self.addons / "matter-server/data"))
        self.assertIn("/data/credentials", matter["command"])
        self.assertIn("/data/updates", matter["command"])

    def test_editor_startup_guard_rejects_missing_credentials(self):
        self.select(("hass-configurator",))
        service = self.config()["services"]["hass-configurator"]
        # Compose config preserves the dollar escape for later container execution.
        script = service["command"][0].replace("$$", "$")
        result = self.command(["/bin/sh", "-ec", script], success=False)
        self.assertIn("HC_USERNAME", result.stderr)
        env = dict(self.env, HC_USERNAME="admin")
        result = self.command(["/bin/sh", "-ec", script], success=False, env=env)
        self.assertIn("HC_PASSWORD", result.stderr)

    def test_editor_receives_stop_signal_directly_and_preserves_settings_argument(self):
        self.select(("hass-configurator",))
        service = self.config()["services"]["hass-configurator"]
        editor = self.bin / "hass-configurator"
        editor.write_text('''#!/usr/bin/env python3
import json, os, pathlib, signal, sys
signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
pathlib.Path(os.environ["EDITOR_READY"]).write_text(json.dumps({"pid": os.getpid(), "args": sys.argv[1:]}))
while True:
    signal.pause()
''')
        editor.chmod(0o755)
        settings = self.root / "editor-settings.conf"
        ready = self.root / "editor-ready.json"
        for has_settings in (False, True):
            with self.subTest(has_settings=has_settings):
                ready.unlink(missing_ok=True)
                if has_settings:
                    settings.write_text("{}")
                script = service["command"][0].replace("$$", "$").replace("/config/settings.conf", str(settings))
                env = dict(self.env, HC_USERNAME="test-user", HC_PASSWORD="test-password", EDITOR_READY=str(ready))
                process = subprocess.Popen([*service["entrypoint"], script], env=env,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 3
                    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(ready.exists(), "editor did not start")
                    state = json.loads(ready.read_text())
                    self.assertEqual(state["pid"], process.pid, "shell must exec the application")
                    self.assertEqual(state["args"], [str(settings)] if has_settings else [])
                    process.send_signal(getattr(signal, service["stop_signal"]))
                    stdout, stderr = process.communicate(timeout=3)
                    self.assertEqual(process.returncode, 0, stdout + stderr)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()

    def test_unknown_profile_fails_before_mutations(self):
        self.select(("misspelled",))
        self.assertIn("Unknown profile", self.addon("apply", success=False).stderr)
        self.assertFalse(self.mutations())

    def test_start_and_repeated_apply_do_not_remove_selected_services(self):
        self.select(ADDON_NAMES)
        self.addon("apply")
        self.log.write_text("")
        self.addon("start")
        self.assertFalse(any("pull" in c for c in self.commands() if c[0] == "compose"))
        self.assertFalse(any(c[0] in ("stop", "rm") for c in self.commands()))
        self.addon("apply")
        self.assertFalse(any(c[0] in ("stop", "rm") for c in self.commands()))

    def test_foreign_container_is_not_adopted_or_removed(self):
        self.select(("zigbee2mqtt",))
        self.addon("apply")
        state = json.loads(self.state.read_text())
        state["zigbee2mqtt"]["Config"]["Labels"]["com.docker.compose.project"] = "legacy-main"
        self.state.write_text(json.dumps(state))
        self.select(())
        self.log.write_text("")
        self.assertIn("another project", self.addon("apply", success=False).stderr)
        self.assertFalse(self.mutations())
        self.assertEqual(json.loads(self.state.read_text()), state)

    def test_pull_failure_keeps_previously_running_services(self):
        self.select(ADDON_NAMES)
        self.addon("apply")
        state = self.state.read_text()
        self.select(("hass-configurator",))
        self.log.write_text("")
        self.addon("apply", success=False, env=dict(self.env, FAKE_FAIL_PULL="1"))
        self.assertEqual(self.state.read_text(), state)
        self.assertFalse(any(c[0] in ("stop", "rm") or "up" in c for c in self.commands()))

    def test_check_is_read_only_and_reports_inactive_container(self):
        self.select(("zigbee2mqtt",))
        self.addon("apply")
        self.select(())
        self.log.write_text("")
        self.assertIn("disabled container", self.addon("check", success=False).stderr)
        self.assertFalse(self.mutations())

    def test_removed_service_cleanup_preserves_other_projects_and_oneoff_jobs(self):
        self.select(("zigbee2mqtt",))
        self.addon("apply")
        state = json.loads(self.state.read_text())
        for name, project, service, oneoff in (
                ("retired", "ha-addons", "retired-service", "False"),
                ("oneoff", "ha-addons", "ad-hoc-service", "True"),
                ("unrelated", "another-project", "unrelated-service", "False")):
            c = json.loads(json.dumps(state["zigbee2mqtt"]))
            c["Id"], c["Name"] = name, "/" + name
            c["Config"]["Labels"] = {"com.docker.compose.project": project,
                                      "com.docker.compose.service": service,
                                      "com.docker.compose.oneoff": oneoff}
            state[name] = c
        self.state.write_text(json.dumps(state))
        self.select(())
        self.addon("apply")
        self.assertEqual(set(json.loads(self.state.read_text())), {"oneoff", "unrelated"})

    def test_check_reports_runtime_and_health_failures_without_mutating(self):
        self.select(("hass-configurator",))
        self.addon("apply")
        state = json.loads(self.state.read_text())
        for reason in ("stopped", "restart", "health"):
            with self.subTest(reason=reason):
                modified = json.loads(json.dumps(state))
                c = modified["hass-configurator"]
                if reason == "stopped":
                    c["State"]["Running"] = False
                elif reason == "restart":
                    c["HostConfig"]["RestartPolicy"]["Name"] = "no"
                else:
                    c["State"]["Health"] = {"Status": "unhealthy"}
                self.state.write_text(json.dumps(modified))
                self.log.write_text("")
                self.addon("check", success=False)
                self.assertFalse(self.mutations())

    def test_documented_whole_system_stop_start_and_restart(self):
        readme = (ROOT / "readme.md").read_text()

        def script(heading):
            section = readme.split("### " + heading + "\n", 1)[1]
            block = re.search(r"```bash\n(.*?)\n```", section, flags=re.S).group(1)
            return block.replace("/home/hauser/home_assistant_config", str(self.root))

        profiles = ("matter-server",)
        self.state.write_text("{}")
        self.select(profiles)
        self.command(["docker", "compose", "--project-directory", str(self.main),
                      "--env-file", os.devnull, "up", "-d", "--pull", "never"])
        self.addon("apply")
        self.command(["docker", "compose", "--project-directory", str(self.root / "tools/portainer"),
                      "--env-file", os.devnull, "up", "-d", "--pull", "never"])
        (self.root / ".env").write_text("COMPOSE_PROFILES=wrong\n")
        self.log.write_text("")
        self.command(["bash", "-ec", script("Остановка всей системы")])
        state = json.loads(self.state.read_text())
        for c in state.values():
            project = c["Config"]["Labels"]["com.docker.compose.project"]
            self.assertEqual(c["State"]["Running"], project == "tools-portainer")
        commands = self.commands()
        stops = [c for c in commands if c[0] == "stop" or c[0] == "compose" and "stop" in c]
        self.assertEqual(stops[0][0], "compose")
        self.assertIn(str(self.addons), stops[0])
        self.assertEqual(stops[0][-6:], ["--profile", "*", "stop", "--timeout", "60", "matter-server"])
        self.assertEqual(stops[1][0], "compose")  # Addons stop before the main stack.
        self.assertIn(str(self.main), stops[1])
        self.assertFalse(any("pull" in c or "up" in c or c[0] == "rm" for c in commands))
        for action in ("Запуск после остановки", "Перезапуск всей системы"):
            self.log.write_text("")
            self.command(["bash", "-ec", script(action)])
            self.assertEqual(self.managed(), set(profiles))
            self.assertTrue(all(c["State"]["Running"] for c in json.loads(self.state.read_text()).values()))
            commands = self.commands()
            self.assertFalse(any("pull" in c for c in commands if c[0] == "compose"))
            starts = [c for c in commands if c[0] == "compose" and "up" in c]
            self.assertNotIn("--project-name", starts[0])  # Main stack starts before addons.
            if profiles:
                self.assertIn("--project-name", starts[-1])
        self.log.write_text("")
        self.command(["bash", "-ec", script("Проверка и расположение каталогов")])
        self.assertFalse(self.mutations())

    def test_stop_shows_compose_progress_and_preserves_oneoff_and_foreign_containers(self):
        self.select(("matter-server",))
        self.addon("apply")
        state = json.loads(self.state.read_text())
        for name, project, service, oneoff in (
                ("retired", "ha-addons", "retired-service", "False"),
                ("oneoff", "ha-addons", "matter-server", "True"),
                ("unrelated", "another-project", "matter-server", "False")):
            container = json.loads(json.dumps(state["matter-server"]))
            container.update(Id=name, Name="/" + name)
            container["Config"]["Labels"].update({
                "com.docker.compose.project": project, "com.docker.compose.service": service,
                "com.docker.compose.oneoff": oneoff,
            })
            state[name] = container
        self.state.write_text(json.dumps(state))
        self.log.write_text("")
        result = self.addon("stop")
        self.assertIn("Container matter-server Stopping", result.stderr)
        self.assertIn("Container matter-server Stopped", result.stderr)
        state = json.loads(self.state.read_text())
        for name in ("matter-server", "retired"):
            self.assertFalse(state[name]["State"]["Running"])
        for name in ("oneoff", "unrelated"):
            self.assertTrue(state[name]["State"]["Running"])
        self.assertTrue(all("pull" not in command and "up" not in command for command in self.mutations()))

    def test_controller_fails_before_stopping_on_invalid_configuration(self):
        (self.main / "docker-compose.yml").write_text("services: [invalid]\n")
        self.command(["bash", str(self.system / "ha.sh"), "restart"], success=False)
        self.assertFalse(self.mutations())

    def test_controller_does_not_start_addons_when_main_start_fails(self):
        self.select(("matter-server",))
        self.command(["bash", str(self.system / "ha.sh"), "start"], success=False,
                     env=dict(self.env, FAKE_FAIL_UP="1"))
        starts = [c for c in self.commands() if c[0] == "compose" and "up" in c]
        self.assertEqual(len(starts), 1)
        self.assertIn(str(self.main), starts[0])
        self.assertEqual(json.loads(self.state.read_text()), {})

    def test_controller_help_and_unknown_action_do_not_access_docker(self):
        self.command(["bash", str(self.system / "ha.sh"), "--help"])
        self.command(["bash", str(self.system / "ha.sh"), "invalid"], success=False)
        self.assertEqual(self.commands(), [])

    def test_autostart_delegates_to_controller(self):
        self.select(("matter-server",))
        args = ["bash", str(self.system / "autostart.sh"), str(self.main),
                str(self.root / "tools"), str(self.addons)]
        self.command(args)
        starts = [c for c in self.commands() if c[0] == "compose" and "up" in c]
        self.assertIn(str(self.main), starts[0])
        self.assertIn(str(self.addons), starts[1])
        self.assertIn(str(self.root / "tools/portainer"), starts[2])
        self.assertFalse(any("pull" in c for c in self.commands() if c[0] == "compose"))
        self.log.write_text("")
        self.command(args[:2] + ["--check"] + args[2:])
        self.assertFalse(self.mutations())

    def test_default_directory_is_independent_of_cwd_and_preserves_dollar_password(self):
        secret = "test-only-$literal#value"
        self.select(("hass-configurator",), "HC_USERNAME=admin\nHC_PASSWORD='" + secret + "'\n")
        (self.root / ".env").write_text("COMPOSE_PROFILES=wrong\n")
        result = self.command(["bash", str(self.system / "addons.sh"), "validate"])
        self.assertNotIn(secret, result.stdout + result.stderr)
        # The canonical config escapes dollars so it can be fed back to Compose.
        rendered = self.config()["services"]["hass-configurator"]["environment"]["HC_PASSWORD"]
        self.assertEqual(rendered.replace("$$", "$"), secret)


if __name__ == "__main__":
    unittest.main()
