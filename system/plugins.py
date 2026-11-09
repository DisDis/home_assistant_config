#!/usr/bin/env python3
"""Reconcile optional Compose services without application-specific configuration logic."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


class PluginError(Exception):
    """A Compose or container lifecycle problem."""


def run(args, *, capture=True):
    result = subprocess.run(args, text=True, capture_output=capture, check=False)
    if result.returncode:
        # Parser errors may contain interpolated values; never dump configuration.
        raise PluginError(f"{args[0]} command failed (exit {result.returncode}); check Docker and Compose settings")
    return result.stdout.strip() if capture else ""


class Plugins:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        if not (self.directory / "docker-compose.yml").is_file():
            raise PluginError(f"Compose file not found in {self.directory}")
        env_file = self.directory / ".env"
        self.command = [
            "docker", "compose", "--project-directory", str(self.directory),
            "--env-file", str(env_file) if env_file.is_file() else os.devnull,
            "-f", str(self.directory / "docker-compose.yml"),
        ]
        # Compose owns dotenv parsing, profiles, dependencies and all app settings.
        self.config = json.loads(self.compose("config", "--format", "json"))
        self.project = self.config["name"]
        self.command += ["--project-name", self.project]
        self.services = self.config.get("services") or {}
        self.all_config = json.loads(run(self.command + ["--profile", "*", "config", "--format", "json"]))
        known_profiles = {profile for service in (self.all_config.get("services") or {}).values()
                          for profile in service.get("profiles", [])}
        selected = self.config.get("x-enabled-profiles") or ""
        if set(filter(None, selected.split(","))) - known_profiles:
            raise PluginError("Unknown profile; check COMPOSE_PROFILES against the profiles in Compose")

    def compose(self, *args, capture=True):
        return run(self.command + list(args), capture=capture)

    def validate(self):
        # No application names, credentials, device paths or app config validation.
        print("Enabled services: " + (", ".join(self.services) or "none"))

    def containers(self):
        ids = run(["docker", "ps", "--all", "--quiet", "--filter",
                   f"label=com.docker.compose.project={self.project}"]).splitlines()
        found = json.loads(run(["docker", "inspect", *ids])) if ids else []
        managed = []
        for container in found:
            labels = container.get("Config", {}).get("Labels") or {}
            if labels.get("com.docker.compose.project") != self.project:
                raise PluginError("Container ownership does not match the Compose project")
            if labels.get("com.docker.compose.oneoff", "false").lower() == "true":
                continue
            if not labels.get("com.docker.compose.service"):
                raise PluginError("Container is missing its Compose service label")
            managed.append(container)
        # Never adopt/remove a same-name container from the old stack or another project.
        for service, spec in (self.all_config.get("services") or {}).items():
            name = spec.get("container_name")
            if not name:
                continue
            matches = run(["docker", "ps", "--all", "--quiet", "--filter",
                           f"name=^/{re.escape(name)}$"]).splitlines()
            if matches:
                container = json.loads(run(["docker", "inspect", *matches]))[0]
                labels = container.get("Config", {}).get("Labels") or {}
                if (labels.get("com.docker.compose.project") != self.project
                        or labels.get("com.docker.compose.service") != service):
                    raise PluginError(f"{service}: name is used by another project; resolve the container_name conflict first")
        return managed

    @staticmethod
    def service_name(container):
        return container["Config"]["Labels"]["com.docker.compose.service"]

    def check(self):
        found = self.containers()
        for container in found:
            service = self.service_name(container)
            if service not in self.services:
                raise PluginError(f"{service}: disabled container still exists; run plugins.sh apply")
        for service in self.services:
            containers = [c for c in found if self.service_name(c) == service]
            if not containers:
                raise PluginError(f"{service}: container is missing")
            for container in containers:
                policy = container["HostConfig"]["RestartPolicy"]["Name"]
                if not container["State"]["Running"] or policy not in ("unless-stopped", "always"):
                    raise PluginError(f"{service}: container must be running with restart unless-stopped or always")
                health = container["State"].get("Health", {}).get("Status")
                if health and health != "healthy":
                    raise PluginError(f"{service}: container is not healthy")
            print(f"{service}: running, restart policy checked")

    def stop_containers(self, containers, *, timeout):
        known = self.all_config.get("services") or {}
        services = sorted({self.service_name(c) for c in containers if self.service_name(c) in known})
        if services:
            # Compose provides container progress and stops in dependency order.
            # All profiles apply only to stop; no images or containers are created.
            run(self.command + ["--profile", "*", "stop", "--timeout", str(timeout), *services], capture=False)
        retired = [c for c in containers if self.service_name(c) not in known]
        if retired:
            names = ", ".join(c.get("Name", c["Id"]).lstrip("/") for c in retired)
            print("Stopping containers absent from Compose: " + names, flush=True)
            # These services no longer have a Compose definition. -t also works on older CLIs.
            run(["docker", "stop", "-t", str(timeout), *[c["Id"] for c in retired]], capture=False)

    def stop(self):
        # Stop the entire managed project, including no-longer-selected services.
        self.stop_containers(self.containers(), timeout=60)
        print("Plugin containers: stopped; data and containers preserved")

    def apply(self, *, pull):
        found = self.containers()
        if self.services and pull:
            # Explicit selection prevents inactive profiles from being downloaded.
            self.compose("pull", *self.services, capture=False)
        disabled = [c for c in found if self.service_name(c) not in self.services]
        if disabled:
            self.stop_containers(disabled, timeout=30)
            run(["docker", "rm", *[c["Id"] for c in disabled]], capture=False)  # No -v: preserve data.
        if self.services:
            self.compose("up", "-d", "--pull", "never", "--wait", "--wait-timeout", "60",
                         *self.services, capture=False)
        else:
            print("No enabled services; image download and startup skipped")
        self.check()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "check", "apply", "start", "stop"),
                        help="validate: Compose only; check: read-only; apply: pull and reconcile; start: no pull; stop: preserve containers")
    root = Path(__file__).resolve().parents[1]
    default_dir = root / "plugins" if (root / "homeassistant/docker-compose.yml").is_file() else root.parent / "plugins"
    parser.add_argument("directory", nargs="?", default=default_dir)
    args = parser.parse_args()
    try:
        plugins = Plugins(args.directory)
        plugins.validate()
        if args.action == "check":
            plugins.check()
        elif args.action == "stop":
            plugins.stop()
        elif args.action in ("apply", "start"):
            plugins.apply(pull=args.action == "apply")
    except (PluginError, OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
