"""Who holds a run, and whether they are still alive (G5)."""

from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path


def _boot_id() -> str:
    # Linux names each boot; elsewhere a pid alone has to do
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


@dataclass(frozen=True)
class Owner:
    """A process: its pid, host, boot and start time, so a pid reused by
    another process, after a reboot or on another machine is not mistaken
    for it. ``boot`` and ``started`` are empty where the platform cannot
    tell (macOS); the pid alone decides there."""

    pid: int
    host: str
    boot: str
    started: str = ""

    @classmethod
    def current(cls) -> Owner:
        pid = os.getpid()
        return cls(pid, socket.gethostname(), _BOOT, _process_start(pid))

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "pid": self.pid,
                "host": self.host,
                "boot": self.boot,
                "started": self.started,
            }
        ).encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> Owner | None:
        try:
            raw = json.loads(data)
            return cls(
                int(raw["pid"]),
                str(raw["host"]),
                str(raw["boot"]),
                # holder files written by 0.2.0 have no start time
                str(raw.get("started", "")),
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            return None

    def is_alive(self) -> bool:
        """False only when this process is known to be gone.

        An owner on another host cannot be checked from here, so it counts
        as alive: a run is never collected on a guess.
        """
        here = Owner.current()
        if self.host != here.host:
            return True
        if self.boot and here.boot and self.boot != here.boot:
            return False
        if not _pid_alive(self.pid):
            return False
        if self.started:
            # a process with this pid exists; is it the one that wrote this?
            now = _process_start(self.pid)
            if now and now != self.started:
                return False
        return True


_BOOT = _boot_id()

if sys.platform == "win32":
    import ctypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _ERROR_ACCESS_DENIED = 5

    class _FileTime(ctypes.Structure):
        _fields_ = (("low", ctypes.c_ulong), ("high", ctypes.c_ulong))

    def _process_start(pid: int) -> str:
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            times = [_FileTime() for _ in range(4)]
            if not _kernel32.GetProcessTimes(
                handle, *(ctypes.byref(time) for time in times)
            ):
                return ""
            return str(times[0].high << 32 | times[0].low)
        finally:
            _kernel32.CloseHandle(handle)

    def _pid_alive(pid: int) -> bool:
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return bool(ctypes.get_last_error() == _ERROR_ACCESS_DENIED)
        try:
            code = ctypes.c_ulong()
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == _STILL_ACTIVE
        finally:
            _kernel32.CloseHandle(handle)

else:

    def _process_start(pid: int) -> str:
        # Linux: field 22 of /proc/{pid}/stat, in clock ticks since boot;
        # the name before it can hold spaces and parentheses
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return ""
        fields = stat.rpartition(")")[2].split()
        return fields[19] if len(fields) > 19 else ""

    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
