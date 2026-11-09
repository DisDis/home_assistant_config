#!/usr/bin/env python3
"""Interactive, offline HA backups with preflight checks and service recovery."""

import argparse
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import traceback

sys.dont_write_bytecode = True
from backup_ui import Reporter, duration, human_size


EXCLUDED = {"backups", ".ha-backup.lock"}


class BackupError(Exception):
    def __init__(self, message, *, details=(), action=None):
        super().__init__(message)
        self.details = list(details)
        self.action = action


class LockedError(BackupError):
    pass


class Interrupted(BackupError):
    def __init__(self, signum):
        self.code = 128 + signum
        super().__init__(f"Получен сигнал {signal.Signals(signum).name}; операция прервана")


@dataclass
class ArchiveTask:
    name: str
    label: str
    parent: Path
    paths: list


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def interrupt(signum, _frame):
    raise Interrupted(signum)


@contextmanager
def checked(report, name):
    item = {"name": name, "status": "in_progress"}
    report["checks"].append(item)
    try:
        yield item
    except Exception as error:
        item.update(status="failed", message=str(error))
        raise
    else:
        item["status"] = "passed"


@contextmanager
def ignore_interrupts():
    handlers = {sig: signal.signal(sig, signal.SIG_IGN)
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def run(args, ui, label, *, discard_stdout=False, progress_file=None):
    """Keep stdout/stderr separate; preserve every line and group failure details."""
    env = dict(os.environ, NO_COLOR="1", COMPOSE_ANSI="never", PYTHONUNBUFFERED="1")
    process = subprocess.Popen(
        [str(arg) for arg in args], env=env, start_new_session=True,
        stdout=subprocess.DEVNULL if discard_stdout else subprocess.PIPE, stderr=subprocess.PIPE,
    )
    streams = [(process.stderr, "STDERR")]
    if process.stdout is not None:
        streams.append((process.stdout, "STDOUT"))
    pending = {channel: b"" for _stream, channel in streams}
    tail = deque(maxlen=12)
    errors = deque(maxlen=20)
    stdout = []
    started = time.monotonic()

    def consume(line, channel):
        line = line.decode("utf-8", errors="replace")
        if not line.strip():
            return
        level = ui.command_line(line, channel)
        tail.append(line)
        if level == "ERROR":
            errors.append(line)
        if channel == "STDOUT":
            stdout.append(line)

    try:
        with selectors.DefaultSelector() as selector:
            for stream, channel in streams:
                selector.register(stream, selectors.EVENT_READ, channel)
            while selector.get_map():
                for key, _events in selector.select(timeout=0.2):
                    channel = key.data
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        if pending[channel]:
                            consume(pending[channel], channel)
                            pending[channel] = b""
                        continue
                    pending[channel] += chunk
                    while b"\n" in pending[channel]:
                        line, pending[channel] = pending[channel].split(b"\n", 1)
                        consume(line, channel)
                size = progress_file.stat().st_size if progress_file and progress_file.exists() else None
                ui.progress(label, time.monotonic() - started, size=size)
        code = process.wait()
        if code:
            details = list(errors or tail)
            text = "\n".join(details).lower()
            reason = ": нет доступа к исходным данным" if "permission denied" in text else (
                ": недостаточно свободного места" if "no space left" in text else "")
            raise BackupError(f"{label}{reason} (код команды {code})", details=details)
        if errors and not ui.verbose:
            ui.warning(f"{label}: команда сообщила о проблеме при коде 0")
            for line in dict.fromkeys(errors):
                ui.show(f"  {line}", "WARN")
        return "\n".join(stdout)
    finally:
        with ignore_interrupts():
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            for stream, _channel in streams:
                stream.close()
            ui.clear()


def plan(main_dir, plugins_dir, timestamp):
    for directory in (main_dir, plugins_dir):
        if not (directory / "docker-compose.yml").is_file():
            raise BackupError(f"Не найден Compose: {directory / 'docker-compose.yml'}")
    for directory in (main_dir / "config", main_dir / "mosquitto"):
        if not directory.is_dir():
            raise BackupError(f"Не найден каталог рабочих данных: {directory}")
    tasks = [ArchiveTask(f"homeassistant-{timestamp}.tar.gz", "Home Assistant + MQTT",
                         main_dir.parent, [main_dir.name])]
    common = []
    for entry in sorted(plugins_dir.iterdir()):
        if entry.name in EXCLUDED | {"__pycache__", ".git"}:
            continue
        if entry.is_symlink():
            raise BackupError(f"Ссылка вместо каталога/файла плагина: {entry}; укажите реальные данные")
        path = str(Path(plugins_dir.name) / entry.name)
        if entry.is_dir() and not entry.name.startswith("."):
            label = entry.name + (" (инструкции)" if entry.name == "yandex-smart-home" else "")
            tasks.append(ArchiveTask(f"plugin-{entry.name}-{timestamp}.tar.gz", label,
                                     plugins_dir.parent, [path]))
        else:
            common.append(path)
    if common:
        tasks.append(ArchiveTask(f"plugins-config-{timestamp}.tar.gz", "Общие настройки",
                                 plugins_dir.parent, common))
    return tasks


@contextmanager
def installation_lock(main_dir, *, create):
    path = main_dir / ".ha-backup.lock"
    try:
        fd = os.open(path, (os.O_RDWR | os.O_CREAT if create else os.O_RDONLY) | os.O_NOFOLLOW, 0o600)
    except FileNotFoundError:
        if create:
            raise
        yield
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockedError("Другой бэкап этой установки уже выполняется") from None
        yield
    finally:
        os.close(fd)


def check_destination(directory):
    """Probe the nearest existing parent; leave no persistent check artifacts."""
    ancestor = directory
    while not ancestor.exists():
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        raise BackupError(f"Путь назначения не является каталогом: {ancestor}")
    try:
        with tempfile.TemporaryDirectory(prefix=".ha-backup-check-", dir=ancestor) as probe:
            with (Path(probe) / "write-test").open("xb") as output:
                output.write(b"backup write check\n")
                output.flush()
                os.fsync(output.fileno())
    except OSError as error:
        raise BackupError(f"Каталог бэкапа недоступен для записи: {directory}", details=[str(error)]) from error
    return shutil.disk_usage(ancestor).free


def check_sources(tasks, ui):
    """Check traversal and actual opens, without following symlinks or reading data."""
    stack = [task.parent / path for task in tasks for path in task.paths]
    failures = []
    count = size = links = 0
    started = time.monotonic()
    while stack:
        path = stack.pop()
        if path.name in EXCLUDED:
            continue
        try:
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                links += 1
            elif stat.S_ISDIR(metadata.st_mode):
                with os.scandir(path) as entries:
                    stack.extend(Path(entry.path) for entry in entries)
            elif stat.S_ISREG(metadata.st_mode):
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
                try:
                    current = os.fstat(fd)
                    if not stat.S_ISREG(current.st_mode):
                        raise OSError(errno.EAGAIN, "Тип файла изменился во время проверки", str(path))
                    size += current.st_size
                    count += 1
                finally:
                    os.close(fd)
            else:
                ui.record(f"Специальный файл: {path}; его содержимое не копируется", "WARN")
        except OSError as error:
            detail = f"{path}: {error.strerror or error}"
            failures.append(detail)
            ui.record(detail, "ERROR")
            if ui.verbose:
                ui.show(detail, "ERROR")
        ui.progress(f"Проверка чтения: {count} файлов", time.monotonic() - started)
    ui.clear()
    if failures:
        raise BackupError(f"Не удалось прочитать {len(failures)} файлов/каталогов", details=failures,
                          action="Проверьте права, ACL и ограничения файловой системы для указанных путей.")
    return {"files": count, "source_bytes": size, "symlinks": links}


def preflight(main_dir, plugins_dir, tasks, controller, ui, report):
    ui.stage(1, "Проверки перед остановкой")
    compose = ["docker", "compose", "--project-directory", main_dir,
               "-f", main_dir / "docker-compose.yml", "--env-file",
               main_dir / ".env" if (main_dir / ".env").is_file() else os.devnull]
    with checked(report, "docker_compose"):
        run(compose + ["config", "--quiet"], ui, "Проверка основного Compose")
        selection = run(["bash", controller.parent / "plugins.sh", "validate", plugins_dir],
                        ui, "Проверка Compose плагинов")
        run(["docker", "info", "--format", "{{.ServerVersion}}"], ui, "Проверка Docker")
    ui.ok("Docker и конфигурации доступны")
    with checked(report, "source_access") as check:
        sources = check_sources(tasks, ui)
        check.update(sources)
    ui.ok(f"Чтение: {sources['files']} файлов, {human_size(sources['source_bytes'])} без сжатия")
    if sources["symlinks"]:
        message = f"Ссылок: {sources['symlinks']}; сохраняются ссылки, их внешние цели не копируются"
        report["warnings"].append(message)
        ui.warning(message)
    for line in selection.splitlines():
        if line.startswith("Enabled services: "):
            report["selected_plugins"] = line.removeprefix("Enabled services: ")
    return sources


def atomic_json(path, value):
    partial = path.with_name(path.name + ".part")
    try:
        with partial.open("w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def record_error(report, ui, error, phase):
    entry = {"phase": phase, "type": type(error).__name__, "message": str(error),
             "details": getattr(error, "details", [])}
    report["errors"].append(entry)
    ui.error(entry["message"], entry["details"])
    if getattr(error, "action", None):
        ui.show(f"Действие: {error.action}", "ERROR")
    if not isinstance(error, (BackupError, OSError)):
        ui.record(traceback.format_exc(), "ERROR")
    return error.code if isinstance(error, Interrupted) else 1


def create_archive(task, destination, ui):
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        run(["tar", "--create", "--gzip", "--acls", "--xattrs", "--numeric-owner",
             "--file", partial, "--directory", task.parent,
             *[f"--exclude={name}" for name in sorted(EXCLUDED)], "--", *task.paths],
            ui, f"Архивирование: {task.label}", progress_file=partial)
        partial.replace(destination)
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError as error:
            ui.warning(f"Не удалось удалить незавершённый архив {partial}: {error}")


def perform_backup(main_dir, plugins_dir, tasks, controller, output, ui, report):
    code = 0
    restart_needed = False
    downtime_start = None
    phase = "stop"
    try:
        ui.stage(2, "Остановка: плагины → Home Assistant и Mosquitto")
        # Fail before stopping if the initial report or log cannot be saved.
        atomic_json(output / "result.json", report)
        if ui.failed:
            raise BackupError("Журнал недоступен для записи")
        restart_needed = True
        report["stop_attempted"] = True
        downtime_start = ui.downtime_start = time.monotonic()
        ui.service_state = "ОСТАНОВКА"
        run(["bash", controller, "stop", main_dir, plugins_dir], ui, "Остановка системы")
        ui.service_state = "ОСТАНОВЛЕНЫ"
        ui.ok("Сервисы остановлены")
        phase = "archive"
        ui.stage(3, "Создание отдельных архивов")
        ui.show(f"{'Компонент':30}  {'Состояние':12}  {'Размер':12}  Время")
        for index, (task, item) in enumerate(zip(tasks, report["archives"]), 1):
            ui.show(f"Архив {index}/{len(tasks)} · {task.name}", "STEP")
            started = time.monotonic()
            item["status"] = "in_progress"
            try:
                create_archive(task, output / task.name, ui)
            except Exception:
                item["status"] = "failed"
                raise
            finally:
                item["duration_seconds"] = round(time.monotonic() - started, 3)
            item.update(status="created", size_bytes=(output / task.name).stat().st_size)
            ui.ok(f"{task.label:30}  {'Создан':12}  {human_size(item['size_bytes']):12}  "
                  f"{duration(item['duration_seconds'])}")
            atomic_json(output / "result.json", report)
            if ui.failed:
                raise BackupError("Журнал недоступен для записи")
        report["backup_status"] = "created"
    except Exception as error:
        code = record_error(report, ui, error, phase)
        if restart_needed:
            report["backup_status"] = "failed"
    finally:
        if restart_needed:
            # Recovery must run even after a partially failed stop, disk-full or Ctrl+C.
            with ignore_interrupts():
                report["restart_attempted"] = True
                ui.service_state = "ЗАПУСК"
                ui.stage(4, "Запуск: Home Assistant и Mosquitto → выбранные плагины")
                if code:
                    ui.show("Запускаю сервисы после ошибки…", "WARN")
                try:
                    run(["bash", controller, "start", main_dir, plugins_dir], ui, "Запуск системы")
                    report["services_status"] = "running"
                    ui.service_state = "ЗАПУЩЕНЫ"
                    ui.ok("Система запущена")
                except Exception as error:
                    report["services_status"] = "start_failed"
                    code = code or 1
                    record_error(report, ui, error, "restart")
                    ui.error("Не удалось запустить систему")
                    ui.show(f"После устранения причины выполните: {report['recovery_command']}", "ERROR")
                finally:
                    report["downtime_seconds"] = round(time.monotonic() - downtime_start, 3)
                    report["downtime_ongoing"] = report["services_status"] != "running"
                    ui.downtime_start = None
    if not code:
        phase = "verify"
        try:
            ui.stage(5, "Проверка архивов и SHA-256; сервисы уже работают")
            checksums = []
            for task, item in zip(tasks, report["archives"]):
                path = output / task.name
                try:
                    run(["tar", "--list", "--gzip", "--file", path], ui,
                        f"Проверка: {task.label}", discard_stdout=True)
                    digest = hashlib.sha256()
                    read = 0
                    started = time.monotonic()
                    with path.open("rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(chunk)
                            read += len(chunk)
                            ui.progress(f"SHA-256: {task.label}", time.monotonic() - started,
                                        percent=100 * read / max(item["size_bytes"], 1))
                    ui.clear()
                    item.update(status="verified", sha256=digest.hexdigest())
                    checksums.append(f"{digest.hexdigest()}  {task.name}\n")
                    ui.ok(f"Проверен: {task.label}")
                except Exception:
                    item["status"] = "verification_failed"
                    raise
            (output / "SHA256SUMS").write_text("".join(checksums), encoding="utf-8")
            report["backup_status"] = "verified"
        except Exception as error:
            report["backup_status"] = "failed"
            code = record_error(report, ui, error, phase)
    return code


def summary(ui, report, output=None):
    ui.show("\nРЕЗУЛЬТАТ", "STEP")
    labels = {"verified": "ПРОВЕРЕН", "created": "АРХИВЫ СОЗДАНЫ, ПРОВЕРКА НЕ ЗАВЕРШЕНА",
              "failed": "НЕ ЗАВЕРШЁН", "not_started": "НЕ НАЧАТ"}
    services = {"unchanged": "НЕ ИЗМЕНЯЛИСЬ", "running": "ЗАПУЩЕНЫ",
                "start_failed": "ЗАПУСК НЕ ПОДТВЕРЖДЁН"}
    level = "OK" if report["status"] in ("success", "ready") else "ERROR" if report["errors"] else "INFO"
    outcome = {"success": "УСПЕХ", "ready": "ГОТОВО К БЭКАПУ", "cancelled": "ОТМЕНЕНО", "failed": "С ОШИБКАМИ"}
    ui.show(f"Итог:     {outcome[report['status']]}", level)
    if report["mode"] == "check":
        ui.show("Проверка: " + ("ПРОЙДЕНА" if report["status"] == "ready" else "НЕ ПРОЙДЕНА"), level)
    else:
        ui.show(f"Бэкап:    {labels[report['backup_status']]}", level)
    ui.show(f"Сервисы:  {services[report['services_status']]}",
            "ERROR" if report["services_status"] == "start_failed" else "INFO")
    total = sum(item["size_bytes"] for item in report["archives"])
    verified = sum(item["status"] == "verified" for item in report["archives"])
    ui.show(f"Архивы:   проверено {verified}/{len(report['archives'])} · {human_size(total)}")
    ongoing = " и продолжается; запуск не подтверждён" if report["downtime_ongoing"] else ""
    ui.show(f"Время:    {duration(report['duration_seconds'])} · "
            f"простой {duration(report['downtime_seconds'])}{ongoing}")
    if output:
        ui.show(f"Каталог:  {output}\nЖурнал:   {output / 'backup.log'}\nОтчёт:    {output / 'result.json'}")
    if report["services_status"] == "start_failed":
        ui.show(f"Восстановление: {report['recovery_command']}", "ERROR")


def finish(report, ui, started, code, output=None):
    report.update(finished_at=now(), duration_seconds=round(time.monotonic() - started, 3), exit_code=code)
    if ui.failed:
        code = record_error(report, ui, BackupError("Не все сообщения удалось записать в журнал"), "report")
    report["exit_code"] = code
    report["status"] = ("ready" if report["mode"] == "check" else "success") if not code else (
        "cancelled" if code == 2 else "failed")
    if output:
        try:
            atomic_json(output / "result.json", report)
            (output / ("SUCCESS" if not code else "FAILED")).write_text(now() + "\n", encoding="utf-8")
        except OSError as error:
            code = record_error(report, ui, error, "report")
            report.update(status="failed", exit_code=code)
    summary(ui, report, output)
    # A write failure in the final summary must not leave a SUCCESS marker behind.
    if output and (code or ui.failed):
        report.update(status="failed", exit_code=code or 1)
        code = code or 1
        try:
            (output / "SUCCESS").unlink(missing_ok=True)
            (output / "FAILED").write_text(now() + "\n", encoding="utf-8")
            atomic_json(output / "result.json", report)
        except OSError:
            ui.error("Не удалось сохранить полный отчёт; проверьте диск и права каталога бэкапа")
    return code


def main():
    root = Path(__file__).resolve().parents[1]
    default_main = root / "homeassistant" if (root / "homeassistant/docker-compose.yml").is_file() else root
    parser = argparse.ArgumentParser(description=__doc__ + " Перед остановкой требуется ввод yes.")
    parser.add_argument("--check", action="store_true", help="только проверки; без остановки и архивов")
    parser.add_argument("--verbose", action="store_true", help="показать полный вывод команд и диагностику")
    parser.add_argument("--plain", action="store_true", help="без цвета, анимации и специальных символов")
    parser.add_argument("homeassistant_dir", nargs="?", type=Path, default=default_main)
    parser.add_argument("plugins_dir", nargs="?", type=Path)
    parser.add_argument("backups_dir", nargs="?", type=Path)
    args = parser.parse_args()
    main_dir = args.homeassistant_dir.resolve()
    plugins_dir = (args.plugins_dir or main_dir.parent / "plugins").resolve()
    backups_dir = (args.backups_dir or main_dir.parent / "backups").resolve()
    controller = Path(__file__).with_name("ha.sh")
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f%z")
    started = time.monotonic()
    encoding = sys.stdout.encoding or "ascii"
    try:
        "✓✗›…".encode(encoding)
        unicode_ok = True
    except UnicodeError:
        unicode_ok = False
    ui = Reporter(verbose=args.verbose, plain=args.plain or not unicode_ok or os.environ.get("TERM") == "dumb",
                  no_color="NO_COLOR" in os.environ)
    report = {"schema_version": 1, "mode": "check" if args.check else "backup", "run_id": timestamp,
              "started_at": now(), "finished_at": None, "status": "in_progress", "exit_code": None,
              "backup_status": "not_started", "services_status": "unchanged", "stop_attempted": False,
              "restart_attempted": False, "downtime_seconds": 0, "downtime_ongoing": False,
              "duration_seconds": 0, "checks": [], "warnings": [], "errors": [], "archives": [],
              "paths": {"homeassistant": str(main_dir), "plugins": str(plugins_dir), "backups": str(backups_dir)},
              "recovery_command": shlex.join(["bash", str(controller), "start", str(main_dir), str(plugins_dir)])}
    output = None
    destination_ready = False
    old_handlers = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        ui.show("HOME ASSISTANT · Проверка готовности" if args.check else "HOME ASSISTANT · Резервное копирование", "STEP")
        with checked(report, "root"):
            if os.geteuid() != 0:
                command = shlex.join(["sudo", "bash", str(Path(__file__).with_suffix(".sh")), *sys.argv[1:]])
                raise BackupError("Для чтения защищённых данных требуется запуск от root через sudo", action=command)
        os.umask(0o077)
        for command in ("bash", "docker", "tar"):
            if not shutil.which(command):
                raise BackupError(f"Не найдена команда: {command}")
        for source in (main_dir, plugins_dir):
            if backups_dir == source or source in backups_dir.parents:
                raise BackupError("Каталог backups должен находиться вне каталогов HA и плагинов")
        tasks = plan(main_dir, plugins_dir, timestamp)
        report["archives"] = [{"name": task.name, "component": task.label, "status": "pending",
                               "size_bytes": 0, "duration_seconds": 0, "sha256": None} for task in tasks]
        with installation_lock(main_dir, create=False):
            report["checks"].append({"name": "lock", "status": "passed"})
            with checked(report, "destination") as check:
                free = check_destination(backups_dir)
                destination_ready = True
                check["free_bytes"] = free
            with checked(report, "free_space"):
                if free < 1024 * 1024:
                    raise BackupError("В каталоге назначения осталось менее 1 МиБ свободного места")
            sources = preflight(main_dir, plugins_dir, tasks, controller, ui, report)
            ui.ok(f"Запись доступна · свободно {human_size(free)}")
            if free < sources["source_bytes"] + 64 * 1024 * 1024:
                message = "Места меньше объёма исходных данных с запасом 64 МиБ; размер после сжатия заранее неизвестен"
                report["warnings"].append(message)
                ui.warning(message)
        if args.check:
            ui.ok("Проверка пройдена. Остановка и архивирование не выполнялись")
            return finish(report, ui, started, 0)
        ui.show(f"\nКопия:       {timestamp}\nНазначение:  {backups_dir / timestamp}")
        ui.show("Состав копии:")
        for task in tasks:
            ui.show(f"  {task.label}: {task.name}")
        ui.show("HACS/Яндекс: компонент и настройки находятся в архиве Home Assistant; "
                "отдельный каталог Яндекса содержит инструкции.")
        ui.show("\nБудут остановлены Home Assistant, Mosquitto и все контейнеры проекта плагинов.\n"
                "После копирования: запуск HA и MQTT, затем плагинов из текущего .env.\n"
                f"Выбранные плагины: {report.get('selected_plugins', 'согласно .env')}", "WARN")
        try:
            confirmed = input("Для согласия на остановку и бэкап введите yes: ").strip() == "yes"
        except EOFError:
            confirmed = False
        if not confirmed:
            ui.show("Бэкап отменён; состояние сервисов не изменено")
            return finish(report, ui, started, 2)
        with installation_lock(main_dir, create=True):
            backups_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            output = backups_dir / timestamp
            output.mkdir(mode=0o700)
            ui.attach(output / "backup.log")
            ui.record("Подтверждение пользователя получено: yes")
            code = perform_backup(main_dir, plugins_dir, tasks, controller, output, ui, report)
            return finish(report, ui, started, code, output)
    except Exception as error:
        code = record_error(report, ui, error, "preflight" if not output else "backup")
        # Keep full diagnostics for failed preflight when the destination is usable.
        # --check, refusal and contention leave no persistent artifacts.
        if not args.check and destination_ready and not isinstance(error, LockedError) and output is None:
            try:
                backups_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                output = backups_dir / timestamp
                output.mkdir(mode=0o700)
                ui.attach(output / "backup.log")
            except OSError as save_error:
                output = None
                ui.error(f"Не удалось сохранить диагностику: {save_error}")
        return finish(report, ui, started, code, output)
    finally:
        ui.close()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
