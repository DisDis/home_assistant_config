"""Profile and lifecycle regression tests. No daemon, images or hardware required."""

import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ((), ("zigbee2mqtt",), ("hass-configurator",), ("zigbee2mqtt", "hass-configurator"))


@unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI is required")
class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ha-compose-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plugins = self.root / "plugins"
        self.plugins.mkdir()
        shutil.copy(ROOT / "plugins/docker-compose.yml", self.plugins)
        self.system = self.root / "homeassistant/system"
        self.system.mkdir(parents=True)
        for name in ("plugins.py", "plugins.sh", "autostart.sh"):
            shutil.copy(ROOT / "system" / name, self.system)
        shutil.copy(ROOT / "homeassistant/docker-compose.yml", self.system.parent)
        (self.system.parent / "config").mkdir()
        for service in ("zigbee2mqtt", "hass-configurator"):
            data = self.plugins / service / "data"
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
        prefixes = ("COMPOSE_", "HC_", "HA_", "PLUGINS_", "ZIGBEE", "HASS_CONFIGURATOR", "MQTT_READY_")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(prefixes)}
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        REAL_DOCKER=shutil.which("docker"), FAKE_DOCKER_LOG=str(self.log),
                        FAKE_DOCKER_STATE=str(self.state), FAKE_SYSTEMCTL_LOG=str(self.root / "systemctl.log"))
        self.select(())

    def select(self, profiles, extra=""):
        (self.plugins / ".env").write_text("COMPOSE_PROFILES=" + ",".join(profiles) + "\n" + extra)

    def command(self, args, *, success=True, env=None, cwd=None):
        result = subprocess.run(args, env=env or self.env, cwd=cwd or self.root,
                                capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def plugin(self, action, **kwargs):
        return self.command(["bash", str(self.system / "plugins.sh"), action, str(self.plugins)], **kwargs)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def mutations(self):
        return [c for c in self.commands() if c[0] in ("stop", "rm")
                or c[0] == "compose" and ("up" in c or "pull" in c)]

    def managed(self):
        return {c["Config"]["Labels"]["com.docker.compose.service"]
                for c in json.loads(self.state.read_text()).values()
                if c["Config"]["Labels"]["com.docker.compose.project"] == "ha-plugins"}

    def config(self, all_profiles=False):
        args = ["docker", "compose", "--project-directory", str(self.plugins), "--env-file", str(self.plugins / ".env"),
                "-f", str(self.plugins / "docker-compose.yml")]
        if all_profiles:
            args += ["--profile", "*"]
        return json.loads(self.command(args + ["config", "--format", "json"]).stdout)

    def assert_selection(self, profiles):
        self.assertEqual(self.managed(), set(profiles))
        for c in self.commands():
            if c[0] == "compose" and "pull" in c:
                self.assertEqual(set(c[c.index("pull") + 1:]), set(profiles))
            if c[0] == "compose" and "up" in c:
                self.assertEqual(set(c[-len(profiles):]), set(profiles))
                self.assertIn("never", c)
        if not profiles:
            self.assertFalse(any(c[0] == "compose" and ("pull" in c or "up" in c) for c in self.commands()))

    def test_all_four_profile_selections(self):
        for profiles in PROFILES:
            with self.subTest(profiles=profiles):
                self.state.write_text("{}")
                self.log.write_text("")
                self.select(profiles)
                self.plugin("validate")
                self.assertFalse(self.mutations())
                self.assertEqual(set(self.config().get("services") or {}), set(profiles))
                self.plugin("apply")
                self.plugin("check")
                self.assert_selection(profiles)

    def test_all_sixteen_profile_transitions_preserve_data(self):
        for before, after in itertools.product(PROFILES, repeat=2):
            with self.subTest(before=before, after=after):
                self.state.write_text("{}")
                self.select(before)
                self.plugin("apply")
                self.select(after)
                self.log.write_text("")
                self.plugin("apply")
                self.assert_selection(after)
                removed = [item for c in self.commands() if c[0] == "rm" for item in c[1:]]
                self.assertEqual(set(removed), {"ha-plugins_" + name for name in set(before) - set(after)})
                for service in ("zigbee2mqtt", "hass-configurator"):
                    self.assertEqual((self.plugins / service / "data/sentinel").read_text(), "existing state")

    def test_arbitrary_plugin_and_group_profile_need_no_script_changes(self):
        compose = self.plugins / "docker-compose.yml"
        addition = '''
  arbitrary-service:
    profiles: [extra]
    image: registry.example.org/vendor/plugin:1.2.3
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
        self.plugin("apply")
        self.assertEqual(self.managed(), {"arbitrary-service", "companion"})
        self.select(())
        self.plugin("apply")
        self.assertEqual(self.managed(), set())

    def test_application_settings_are_not_validated_by_helper(self):
        self.select(("zigbee2mqtt", "hass-configurator"), "HC_PASSWORD=''\nZIGBEE_ADAPTER_PATH=/no/such/device\nHA_DIR=/missing/main\n")
        self.plugin("validate")
        self.assertFalse(self.mutations())

    def test_data_mounts_are_separate_and_editor_shares_live_ha_config(self):
        services = self.config(all_profiles=True)["services"]
        for service, target in (("zigbee2mqtt", "/app/data"), ("hass-configurator", "/config")):
            source = next(v["source"] for v in services[service]["volumes"] if v["target"] == target)
            self.assertEqual(Path(source), self.plugins / service / "data")
        shared = next(v for v in services["hass-configurator"]["volumes"] if v["target"] == "/hass-config")
        self.assertEqual(Path(shared["source"]), self.system.parent / "config")
        self.assertFalse(shared.get("bind", {}).get("create_host_path", False))
        for service in services.values():
            self.assertEqual(service["logging"]["options"], {"max-size": "10m", "max-file": "3"})
            self.assertEqual(service["ports"][0]["host_ip"], "127.0.0.1")
            self.assertNotIn(":latest", service["image"])

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

    def test_unknown_profile_fails_before_mutations(self):
        self.select(("misspelled",))
        self.assertIn("Unknown profile", self.plugin("apply", success=False).stderr)
        self.assertFalse(self.mutations())

    def test_start_and_repeated_apply_do_not_remove_selected_services(self):
        self.select(PROFILES[-1])
        self.plugin("apply")
        self.log.write_text("")
        self.plugin("start")
        self.assertFalse(any("pull" in c for c in self.commands() if c[0] == "compose"))
        self.assertFalse(any(c[0] in ("stop", "rm") for c in self.commands()))
        self.plugin("apply")
        self.assertFalse(any(c[0] in ("stop", "rm") for c in self.commands()))

    def test_foreign_container_is_not_adopted_or_removed(self):
        self.select(("zigbee2mqtt",))
        self.plugin("apply")
        state = json.loads(self.state.read_text())
        state["zigbee2mqtt"]["Config"]["Labels"]["com.docker.compose.project"] = "legacy-main"
        self.state.write_text(json.dumps(state))
        self.select(())
        self.log.write_text("")
        self.assertIn("another project", self.plugin("apply", success=False).stderr)
        self.assertFalse(self.mutations())
        self.assertEqual(json.loads(self.state.read_text()), state)

    def test_pull_failure_keeps_previously_running_services(self):
        self.select(PROFILES[-1])
        self.plugin("apply")
        state = self.state.read_text()
        self.select(("hass-configurator",))
        self.log.write_text("")
        self.plugin("apply", success=False, env=dict(self.env, FAKE_FAIL_PULL="1"))
        self.assertEqual(self.state.read_text(), state)
        self.assertFalse(any(c[0] in ("stop", "rm") or "up" in c for c in self.commands()))

    def test_check_is_read_only_and_reports_inactive_container(self):
        self.select(("zigbee2mqtt",))
        self.plugin("apply")
        self.select(())
        self.log.write_text("")
        self.assertIn("disabled container", self.plugin("check", success=False).stderr)
        self.assertFalse(self.mutations())

    def test_removed_service_cleanup_preserves_other_projects_and_oneoff_jobs(self):
        self.select(("zigbee2mqtt",))
        self.plugin("apply")
        state = json.loads(self.state.read_text())
        for name, project, service, oneoff in (
                ("retired", "ha-plugins", "retired-service", "False"),
                ("oneoff", "ha-plugins", "ad-hoc-service", "True"),
                ("unrelated", "another-project", "unrelated-service", "False")):
            c = json.loads(json.dumps(state["zigbee2mqtt"]))
            c["Id"], c["Name"] = name, "/" + name
            c["Config"]["Labels"] = {"com.docker.compose.project": project,
                                      "com.docker.compose.service": service,
                                      "com.docker.compose.oneoff": oneoff}
            state[name] = c
        self.state.write_text(json.dumps(state))
        self.select(())
        self.plugin("apply")
        self.assertEqual(set(json.loads(self.state.read_text())), {"oneoff", "unrelated"})

    def test_check_reports_runtime_and_health_failures_without_mutating(self):
        self.select(("hass-configurator",))
        self.plugin("apply")
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
                self.plugin("check", success=False)
                self.assertFalse(self.mutations())

    def test_autostart_all_profiles_runs_main_first_without_downloads(self):
        for profiles in PROFILES:
            with self.subTest(profiles=profiles):
                self.state.write_text("{}")
                self.log.write_text("")
                self.select(profiles)
                args = ["bash", str(self.system / "autostart.sh"), str(self.system.parent),
                        str(self.root / "tools"), str(self.plugins)]
                self.command(args)
                commands = self.commands()
                starts = [c for c in commands if c[0] == "compose" and "up" in c]
                self.assertEqual(starts[0][starts[0].index("--project-directory") + 1], str(self.system.parent))
                self.assertFalse(any("pull" in c for c in commands if c[0] == "compose"))
                self.assertEqual(self.managed(), set(profiles))
                self.log.write_text("")
                self.command(args[:2] + ["--check"] + args[2:])
                self.assertFalse(self.mutations())

    def test_default_directory_is_independent_of_cwd_and_preserves_dollar_password(self):
        secret = "test-only-$literal#value"
        self.select(("hass-configurator",), "HC_USERNAME=admin\nHC_PASSWORD='" + secret + "'\n")
        (self.root / ".env").write_text("COMPOSE_PROFILES=wrong\n")
        result = self.command(["bash", str(self.system / "plugins.sh"), "validate"])
        self.assertNotIn(secret, result.stdout + result.stderr)
        # The canonical config escapes dollars so it can be fed back to Compose.
        rendered = self.config()["services"]["hass-configurator"]["environment"]["HC_PASSWORD"]
        self.assertEqual(rendered.replace("$$", "$"), secret)


if __name__ == "__main__":
    unittest.main()
