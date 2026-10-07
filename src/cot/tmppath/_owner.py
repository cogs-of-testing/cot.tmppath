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
    """A process: its pid, host and boot, so a reused pid after a reboot
    or on another machine is not mistaken for it."""

    pid: int
    host: str
    boot: str

    @classmethod
    def current(cls) -> Owner:
        return cls(os.getpid(), socket.gethostname(), _BOOT)

    def to_bytes(self) -> bytes:
        return json.dumps(
            {"pid": self.pid, "host": self.host, "boot": self.boot}
        ).encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> Owner | None:
        try:
            raw = json.loads(data)
            return cls(int(raw["pid"]), str(raw["host"]), str(raw["boot"]))
        except (ValueError, KeyError, TypeError):
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
        return _pid_alive(self.pid)


_BOOT = _boot_id()

if sys.platform == "win32":
    import ctypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _ERROR_ACCESS_DENIED = 5

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

    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
