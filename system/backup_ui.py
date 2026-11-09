"""Console rendering and a private, append-only diagnostic log for backups."""

from datetime import datetime
import re
import os
import shutil
import sys
import time


ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ERROR = re.compile(r"permission denied|cannot open|no space left|\berror\b|\bfatal\b|failure|failed", re.I)
WARNING = re.compile(r"\bwarning\b|\bwarn\b|deprecated", re.I)


def duration(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


def human_size(size):
    for unit in ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ"):
        if size < 1024 or unit == "ТиБ":
            return f"{size:.1f} {unit}"
        size /= 1024


class Reporter:
    def __init__(self, *, verbose=False, plain=False, no_color=False):
        self.verbose = verbose
        self.plain = plain
        self.tty = sys.stdout.isatty() and not plain
        self.color = self.tty and not no_color
        self.file = None
        self.events = []
        self.warnings = []
        self.failed = False
        self.console = True
        self.active_width = 0
        self.last_snapshot = time.monotonic()
        self.last_draw = 0
        self.service_state = "НЕ ИЗМЕНЯЛИСЬ"
        self.downtime_start = None

    def symbol(self, name):
        if self.plain:
            return {"ok": "[OK]", "error": "[ERROR]", "step": ">", "wait": "..."}[name]
        return {"ok": "✓", "error": "✗", "step": "›", "wait": "…"}[name]

    def record(self, message, level="INFO"):
        # Child processes must not inject terminal control codes into the log/UI.
        message = ANSI.sub("", str(message)).replace("\r", " ")
        message = "".join(c for c in message if c >= " " or c in "\n\t")
        line = f"{datetime.now().astimezone():%Y-%m-%d %H:%M:%S%z} [{level:5}] {message}"
        if self.file is None:
            self.events.append(line)
        else:
            try:
                self.file.write(line + "\n")
                self.file.flush()
            except OSError:
                self.failed = True
        return message

    def attach(self, path):
        self.file = path.open("x", encoding="utf-8")
        for line in self.events:
            self.file.write(line + "\n")
        self.file.flush()
        self.events.clear()

    def clear(self):
        if self.active_width and self.console:
            try:
                sys.stdout.write("\r" + " " * self.active_width + "\r")
                sys.stdout.flush()
            except OSError:
                self.console = False
        self.active_width = 0

    def show(self, message, level="INFO"):
        message = self.record(message, level)
        if self.plain:
            message = message.replace("·", "|").replace("→", "->").replace("…", "...")
        self.clear()
        if not self.console:
            return
        try:
            shade = {"ERROR": "31", "OK": "32", "STEP": "36", "WARN": "33"}.get(level, "0")
            print(f"\033[{shade}m{message}\033[0m" if self.color else message,
                  file=sys.stderr if level == "ERROR" else sys.stdout, flush=True)
        except (OSError, UnicodeError):
            self.console = False

    def stage(self, number, title):
        self.show(f"\n{self.symbol('step')} Этап {number}/5  {title}", "STEP")

    def detail(self, message):
        if self.verbose:
            self.show(message, "DEBUG")
        else:
            self.record(message, "DEBUG")

    def composition(self, entries):
        self.show(f"\nСостав копии ({len(entries)} архивов):")
        for index, (label, filename) in enumerate(entries, 1):
            self.show(f"  {index:>2}. {label}")
            self.detail(f"      Архив: {filename}")

    def ok(self, message):
        self.show(f"{self.symbol('ok')} {message}", "OK")

    def warning(self, message):
        self.warnings.append(message)
        self.show(f"ПРЕДУПРЕЖДЕНИЕ: {message}", "WARN")

    def error(self, message, details=()):
        self.show(f"{self.symbol('error')} ОШИБКА: {message}", "ERROR")
        details = list(dict.fromkeys(str(detail) for detail in details))
        for detail in details[:3]:
            self.show(f"  {detail}", "ERROR")
        if len(details) > 3:
            suffix = "подробности в backup.log" if self.file else "подробности доступны с --verbose"
            self.show(f"  … ещё {len(details) - 3}; {suffix}", "ERROR")

    def command_line(self, line, channel, *, display=True):
        level = "ERROR" if ERROR.search(line) else "WARN" if WARNING.search(line) else "DEBUG"
        if level == "WARN":
            self.warnings.append(line)
        if display and (self.verbose or level == "WARN"):
            self.show(f"  {channel}: {line}", level)
        else:
            self.record(f"{channel}: {line}", level)
        return level

    def relay(self, chunk, *, stderr=False):
        """Relay Docker's own terminal output verbatim; log it separately."""
        if not self.console:
            return
        stream = sys.stderr if stderr else sys.stdout
        try:
            stream.flush()
            view = memoryview(chunk)
            while view:
                view = view[os.write(stream.fileno(), view):]
        except OSError:
            self.console = False

    def progress(self, label, elapsed, *, size=None, percent=None):
        now = time.monotonic()
        if self.tty and now - self.last_draw < 0.2:
            return
        suffix = f" · {duration(elapsed)}"
        if size is not None:
            suffix += f" · записано {human_size(size)}"
        if percent is not None:
            suffix += f" · {percent:.0f}%"
        suffix += f" · Сервисы: {self.service_state}"
        if self.downtime_start is not None:
            suffix += f" · простой {duration(now - self.downtime_start)}"
        indicator = "|/-\\"[int(now * 4) % 4] if self.tty else self.symbol("wait")
        text = f"{indicator} {label}{suffix}"
        snapshot = now - self.last_snapshot >= 5
        if self.tty and self.console:
            width = max(10, shutil.get_terminal_size(fallback=(80, 24)).columns - 1)
            short = text if len(text) <= width else text[:width - 3] + "..."
            try:
                sys.stdout.write("\r" + short.ljust(self.active_width))
                sys.stdout.flush()
                self.active_width = len(short)
                self.last_draw = now
            except (OSError, UnicodeError):
                self.console = False
            if snapshot:
                self.record(text, "PROG")
        elif snapshot:
            self.show(text, "PROG")
        if snapshot:
            self.last_snapshot = now

    def close(self):
        self.clear()
        if self.file is not None:
            try:
                self.file.close()
            except OSError:
                self.failed = True
            finally:
                self.file = None
