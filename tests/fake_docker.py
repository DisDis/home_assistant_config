#!/usr/bin/env python3
"""Test-only Docker daemon substitute; config parsing always uses real Compose."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys


args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
state_file = Path(os.environ["FAKE_DOCKER_STATE"])
state = json.loads(state_file.read_text())


def save():
    state_file.write_text(json.dumps(state))


def container(target):
    return next(c for name, c in state.items() if target in (name, c["Id"]))


if args[0] == "compose":
    operations = {"config", "version", "pull", "up", "stop", "ps", "logs"}
    index = next(i for i, arg in enumerate(args) if arg in operations)
    operation = args[index]
    if operation in ("config", "version"):
        os.execv(os.environ["REAL_DOCKER"], [os.environ["REAL_DOCKER"], *args])
    config = json.loads(subprocess.check_output(
        [os.environ["REAL_DOCKER"], *args[:index], "config", "--format", "json"], text=True))
    services = config.get("services") or {}
    selected = [arg for arg in args[index + 1:] if arg in services] or list(services)
    if operation == "pull":
        sys.exit(1 if os.environ.get("FAKE_FAIL_PULL") else 0)
    if operation == "up":
        if os.environ.get("FAKE_FAIL_UP"):
            sys.exit(1)
        for service in selected:
            spec = services[service]
            name = spec.get("container_name", config["name"] + "-" + service + "-1")
            state[name] = {
                "Id": config["name"] + "_" + service,
                "Name": "/" + name,
                "Config": {"Labels": {
                    "com.docker.compose.project": config["name"],
                    "com.docker.compose.service": service,
                    "com.docker.compose.oneoff": "False",
                }},
                "State": {"Running": True, "Status": "running"},
                "HostConfig": {"RestartPolicy": {"Name": spec.get("restart", "no")}},
            }
        save()
    elif operation == "stop":
        for c in state.values():
            labels = c["Config"]["Labels"]
            if (labels["com.docker.compose.project"] == config["name"]
                    and labels["com.docker.compose.service"] in selected):
                c["State"] = {"Running": False, "Status": "exited"}
        save()
    elif operation == "ps":
        for c in state.values():
            labels = c["Config"]["Labels"]
            if (labels["com.docker.compose.project"] == config["name"]
                    and labels["com.docker.compose.service"] in selected):
                print(c["Id"])
    elif operation != "logs":
        raise RuntimeError(args)
elif args[0] == "ps":
    filters = [args[i + 1] for i, arg in enumerate(args) if arg == "--filter"]
    for name, c in state.items():
        match = True
        for f in filters:
            if f.startswith("name="):
                match &= re.search(f[5:], "/" + name) is not None
            elif f.startswith("label="):
                key, value = f[6:].split("=", 1)
                match &= c["Config"]["Labels"].get(key) == value
            else:
                raise RuntimeError(f)
        if match:
            print(c["Id"])
elif args[0] == "inspect":
    if "--format" in args:
        c = container(args[-1])
        template = args[args.index("--format") + 1]
        if template == "{{.State.Status}}":
            print(c["State"]["Status"])
        elif template == "{{.HostConfig.RestartPolicy.Name}}":
            print(c["HostConfig"]["RestartPolicy"]["Name"])
        else:
            raise RuntimeError(template)
    else:
        print(json.dumps([container(arg) for arg in args[1:]]))
elif args[0] in ("stop", "rm"):
    assert "-v" not in args and "--volumes" not in args
    targets = args[3:] if args[0] == "stop" else args[1:]
    for target in targets:
        c = container(target)
        if args[0] == "stop":
            c["State"] = {"Running": False, "Status": "exited"}
        else:
            name = next(name for name, value in state.items() if value["Id"] == c["Id"])
            del state[name]
    save()
elif args[0] != "info":
    raise RuntimeError("Unexpected Docker command: " + repr(args))
