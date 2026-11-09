#!/usr/bin/env python3
"""Back up HA and each plugin separately, with consent and service recovery."""

import argparse
from datetime import datetime
import fcntl
import hashlib
import os
from pathlib import Path
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import time


class BackupError(Exception):
    """A backup or lifecycle operation failed."""


class Interrupted(BackupError):
    def __init__(self, signum):
        self.code = 128 + signum
        super().__init__(f"Получен сигнал {signal.Signals(signum).name}; бэкап прерван")


def interrupt(signum, _frame):
    raise Interrupted(signum)


class Log:
    def __init__(self, path):
        self.file = path.open("x", encoding="utf-8")
        self.failed = False
        self.console = True
        self.color = sys.stdout.isatty() and "NO_COLOR" not in os.environ

    def write(self, message, level="INFO"):
        line = f"{datetime.now().astimezone():%Y-%m-%d %H:%M:%S%z} [{level:5}] {message}"
        try:
            self.file.write(line + "\n")
            self.file.flush()
        except OSError as error:
            if not self.failed:
                print(f"ОШИБКА записи журнала: {error}", file=sys.stderr)
            self.failed = True
        if self.console:
            try:
                shade = {"ERROR": "31", "OK": "32", "STEP": "36", "WARN": "33"}.get(level, "0")
                print(f"\033[{shade}m{line}\033[0m" if self.color else line,
                      file=sys.stderr if level == "ERROR" else sys.stdout, flush=True)
            except OSError:
                # A closed output pipe must not prevent service recovery.
                self.console = False


def human_size(size):
    for unit in ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ"):
        if size < 1024 or unit == "ТиБ":
            return f"{size:.1f} {unit}"
        size /= 1024


def run(args, log, label, *, discard_stdout=False, progress_file=None):
    """Stream diagnostics, report long operations, and reap the entire child group."""
    env = dict(os.environ, NO_COLOR="1", COMPOSE_ANSI="never", PYTHONUNBUFFERED="1")
    process = subprocess.Popen(
        [str(arg) for arg in args], env=env, start_new_session=True,
        stdout=subprocess.DEVNULL if discard_stdout else subprocess.PIPE,
        stderr=subprocess.PIPE if discard_stdout else subprocess.STDOUT,
    )
    stream = process.stderr if discard_stdout else process.stdout
    pending = b""
    started = last_report = time.monotonic()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                for key, _events in selector.select(timeout=0.2):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        log.write("│ " + line.decode("utf-8", errors="replace"))
                now = time.monotonic()
                if now - last_report >= 5:
                    size = ""
                    if progress_file is not None and progress_file.exists():
                        size = f", записано {human_size(progress_file.stat().st_size)}"
                    log.write(f"… {label}: {int(now - started)} с{size}")
                    last_report = now
        if pending:
            log.write("│ " + pending.decode("utf-8", errors="replace"))
        code = process.wait()
        if code:
            raise BackupError(f"{label}: команда {args[0]} завершилась с кодом {code}")
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        stream.close()


def archive(destination, parent, paths, log):
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        run(["tar", "--create", "--gzip", "--acls", "--xattrs", "--numeric-owner",
             "--file", partial, "--directory", parent,
             "--exclude=backups", "--exclude=.ha-backup.lock", "--", *paths],
            log, destination.name, progress_file=partial)
        partial.replace(destination)
        log.write(f"✓ {destination.name} — {human_size(destination.stat().st_size)}", "OK")
    finally:
        if partial.exists():
            try:
                partial.unlink()
            except OSError as error:
                log.write(f"Не удалось удалить незавершённый архив {partial}: {error}", "ERROR")


def plan(main_dir, plugins_dir):
    for directory in (main_dir, plugins_dir):
        if not (directory / "docker-compose.yml").is_file():
            raise BackupError(f"Не найден Compose: {directory / 'docker-compose.yml'}")
    for directory in (main_dir / "config", main_dir / "mosquitto"):
        if not directory.is_dir():
            raise BackupError(f"Не найден каталог рабочих данных: {directory}")
    tasks = [("homeassistant.tar.gz", main_dir.parent, [main_dir.name])]
    common = []
    for entry in sorted(plugins_dir.iterdir()):
        if entry.name in ("backups", "__pycache__", ".git", ".ha-backup.lock"):
            continue
        if entry.is_symlink():
            raise BackupError(f"Ссылка вместо каталога/файла плагина: {entry}; укажите реальные данные")
        if entry.is_dir() and not entry.name.startswith("."):
            tasks.append((f"plugin-{entry.name}.tar.gz", plugins_dir.parent,
                          [str(Path(plugins_dir.name) / entry.name)]))
        else:
            common.append(str(Path(plugins_dir.name) / entry.name))
    if common:
        tasks.append(("plugins-config.tar.gz", plugins_dir.parent, common))
    return tasks


def backup(main_dir, plugins_dir, output, tasks, controller, log):
    started = time.monotonic()
    restart_needed = False
    code = 0
    try:
        log.write("[1/5] Проверка Docker и конфигураций", "STEP")
        compose = ["docker", "compose", "--project-directory", main_dir,
                   "-f", main_dir / "docker-compose.yml", "--env-file",
                   main_dir / ".env" if (main_dir / ".env").is_file() else os.devnull]
        run(compose + ["config", "--quiet"], log, "Проверка основного Compose")
        run(["bash", controller.parent / "plugins.sh", "validate", plugins_dir],
            log, "Проверка Compose плагинов")
        run(["docker", "info", "--format", "{{.ServerVersion}}"], log, "Проверка Docker")
        if log.failed:
            raise BackupError("Журнал недоступен для записи")

        log.write("[2/5] Остановка: плагины → Home Assistant и Mosquitto", "STEP")
        # Arm recovery before stop: it can fail after stopping some containers.
        restart_needed = True
        run(["bash", controller, "stop", main_dir, plugins_dir], log, "Остановка системы")
        log.write("[3/5] Создание отдельных архивов при остановленной системе", "STEP")
        for index, (name, parent, paths) in enumerate(tasks, 1):
            log.write(f"[{index}/{len(tasks)}] {name}", "STEP")
            archive(output / name, parent, paths, log)
            if log.failed:
                raise BackupError("Журнал недоступен для записи")
    except (BackupError, OSError) as error:
        code = error.code if isinstance(error, Interrupted) else 1
        log.write(str(error), "ERROR")
    finally:
        if restart_needed:
            # Let recovery finish even if Ctrl+C is pressed again.
            handlers = {sig: signal.signal(sig, signal.SIG_IGN)
                        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
            try:
                log.write("[4/5] Запуск: Home Assistant и Mosquitto → выбранные плагины", "STEP")
                run(["bash", controller, "start", main_dir, plugins_dir], log, "Запуск системы")
                log.write("✓ Система запущена", "OK")
            except (BackupError, OSError) as error:
                code = code or 1
                log.write(f"Не удалось запустить систему: {error}", "ERROR")
                command = shlex.join(["bash", str(controller), "start", str(main_dir), str(plugins_dir)])
                log.write(f"После устранения причины выполните: {command}", "ERROR")
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)

    if not code:
        try:
            log.write("[5/5] Проверка архивов и SHA-256; сервисы уже работают", "STEP")
            checksums = []
            for name, _parent, _paths in tasks:
                path = output / name
                run(["tar", "--list", "--gzip", "--file", path], log,
                    f"Проверка {name}", discard_stdout=True)
                digest = hashlib.sha256()
                last_report = time.monotonic()
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                        if time.monotonic() - last_report >= 5:
                            log.write(f"… SHA-256: {name}")
                            last_report = time.monotonic()
                checksums.append(f"{digest.hexdigest()}  {name}\n")
                log.write(f"✓ Проверен {name}", "OK")
            (output / "SHA256SUMS").write_text("".join(checksums), encoding="utf-8")
            log.write(f"✓ Бэкап завершён за {int(time.monotonic() - started)} с: {output}", "OK")
            if log.failed:
                raise BackupError("Не все сообщения удалось записать в журнал")
            (output / "SUCCESS").write_text(f"{datetime.now().astimezone().isoformat()}\n", encoding="utf-8")
        except (BackupError, OSError) as error:
            code = error.code if isinstance(error, Interrupted) else 1
            log.write(str(error), "ERROR")
    if code:
        try:
            (output / "FAILED").write_text("Бэкап не завершён. Подробности в backup.log.\n", encoding="utf-8")
        except OSError as error:
            log.write(f"Не удалось записать FAILED: {error}", "ERROR")
        log.write(f"Бэкап не завершён. Каталог: {output}", "ERROR")
    return code


def main():
    root = Path(__file__).resolve().parents[1]
    default_main = root / "homeassistant" if (root / "homeassistant/docker-compose.yml").is_file() else root
    parser = argparse.ArgumentParser(
        description="Отдельные бэкапы HA и плагинов с остановкой и запуском через ha.sh. "
                    "Перед остановкой обязательно введите yes; отказ/EOF отменяют бэкап.",
    )
    parser.add_argument("homeassistant_dir", nargs="?", type=Path, default=default_main)
    parser.add_argument("plugins_dir", nargs="?", type=Path)
    parser.add_argument("backups_dir", nargs="?", type=Path,
                        help="по умолчанию backups/ рядом с каталогом Home Assistant")
    args = parser.parse_args()
    main_dir = args.homeassistant_dir.resolve()
    plugins_dir = (args.plugins_dir or main_dir.parent / "plugins").resolve()
    backups_dir = (args.backups_dir or main_dir.parent / "backups").resolve()
    try:
        for command in ("bash", "docker", "tar"):
            if not shutil.which(command):
                raise BackupError(f"Не найдена команда: {command}")
        for source in (main_dir, plugins_dir):
            if backups_dir == source or source in backups_dir.parents:
                raise BackupError("Каталог backups должен находиться вне каталогов HA и плагинов")
        tasks = plan(main_dir, plugins_dir)
        print("\nНа время бэкапа будут остановлены Home Assistant, Mosquitto и все контейнеры "
              "проекта плагинов. После копирования они будут запущены через ha.sh start "
              "с текущим выбором плагинов.", flush=True)
        print(f"HA:      {main_dir}\nПлагины: {plugins_dir}\nБэкапы:  {backups_dir}")
        print("Архивы: " + ", ".join(task[0] for task in tasks))
        print("HACS/Яндекс: рабочие данные входят в homeassistant.tar.gz; "
              "plugin-yandex-smart-home.tar.gz содержит только каталог из plugins/.")
        try:
            confirmed = input("Для согласия на остановку и бэкап введите yes: ").strip() == "yes"
        except EOFError:
            confirmed = False
        if not confirmed:
            print("Бэкап отменён; состояние сервисов не изменено.")
            return 2

        os.umask(0o077)
        # A per-installation lock also excludes runs using another backups_dir.
        with (main_dir / ".ha-backup.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BackupError("Другой бэкап этой установки уже выполняется") from None
            backups_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            output = backups_dir / datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f%z")
            output.mkdir(mode=0o700)
            log = Log(output / "backup.log")
            try:
                log.write("Подтверждение пользователя получено: yes")
                log.write(f"Каталог бэкапа: {output}")
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                    signal.signal(sig, interrupt)
                return backup(main_dir, plugins_dir, output, tasks, root / "system/ha.sh", log)
            finally:
                log.file.close()
    except (BackupError, OSError) as error:
        print(f"ОШИБКА: {error}", file=sys.stderr)
        return error.code if isinstance(error, Interrupted) else 1
    except KeyboardInterrupt:
        print("\nБэкап отменён до остановки сервисов.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
