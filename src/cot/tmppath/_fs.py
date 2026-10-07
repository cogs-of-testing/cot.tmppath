"""Directory handles: every operation below a checked folder goes through it.

Where the platform has them, a handle holds an open directory descriptor and
works with ``dir_fd`` and ``O_NOFOLLOW``, so the folder that was checked is
the folder that is used, whatever happens to its path afterwards (G1).
Elsewhere (Windows) it falls back to paths and ``lstat``.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing_extensions import Self

#: whether directory descriptors back the handles on this platform
USE_FD = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and {os.mkdir, os.open, os.rename, os.stat, os.unlink} <= os.supports_dir_fd
    and os.scandir in os.supports_fd
)

_DIR_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_FILE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_BINARY", 0)
)
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_BINARY", 0)
)
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class NotADirectoryHere(OSError):
    """The name is a symlink, a junction or not a directory at all."""


def is_link(st: os.stat_result) -> bool:
    """A symlink, or on Windows any reparse point (junctions included)."""
    attributes = getattr(st, "st_file_attributes", 0)
    return stat.S_ISLNK(st.st_mode) or bool(attributes & _REPARSE_POINT)


class Dir:
    """An open, checked directory. Close it when done."""

    def __init__(self, path: Path, fd: int | None) -> None:
        self.path = path
        self._fd = fd

    @classmethod
    def at(cls, path: Path) -> Dir:
        """Open a trusted base folder, following symlinks (``/tmp`` on macOS)."""
        if USE_FD:
            return cls(path, os.open(path, _DIR_FLAGS & ~getattr(os, "O_NOFOLLOW", 0)))
        return cls(path, None)

    @classmethod
    def open(cls, path: Path) -> Dir:
        """Open ``path`` itself without following a symlink at its last part."""
        if USE_FD:
            parent = os.open(path.parent, _DIR_FLAGS & ~getattr(os, "O_NOFOLLOW", 0))
            try:
                return cls(path, _open_dir(path.name, parent))
            finally:
                os.close(parent)
        _check_dir(path)
        return cls(path, None)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def stat(self) -> os.stat_result:
        if self._fd is not None:
            return os.fstat(self._fd)
        return self.path.lstat()

    def chmod(self, mode: int) -> None:
        if self._fd is not None:
            os.fchmod(self._fd, mode)
        else:
            self.path.chmod(mode)

    def mkdir(self, name: str) -> Path:
        """Create ``name``, private; raises ``FileExistsError`` if taken."""
        if self._fd is not None:
            os.mkdir(name, 0o700, dir_fd=self._fd)
        else:
            (self.path / name).mkdir(0o700)
        return self.path / name

    def open_dir(self, name: str) -> Dir:
        if self._fd is not None:
            return Dir(self.path / name, _open_dir(name, self._fd))
        path = self.path / name
        _check_dir(path)
        return Dir(path, None)

    def is_dir(self, name: str) -> bool:
        try:
            st = self._lstat(name)
        except FileNotFoundError:
            return False
        return stat.S_ISDIR(st.st_mode) and not is_link(st)

    def _lstat(self, name: str) -> os.stat_result:
        if self._fd is not None:
            return os.stat(name, dir_fd=self._fd, follow_symlinks=False)
        return (self.path / name).lstat()

    def mtime(self, name: str) -> float:
        return self._lstat(name).st_mtime

    def create_file(self, name: str, data: bytes) -> None:
        """Write a new file; raises ``FileExistsError`` if the name is taken."""
        if self._fd is not None:
            fd = os.open(name, _FILE_FLAGS, 0o600, dir_fd=self._fd)
        else:
            fd = os.open(self.path / name, _FILE_FLAGS, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    def read_file(self, name: str) -> bytes | None:
        try:
            if self._fd is not None:
                fd = os.open(name, _READ_FLAGS, dir_fd=self._fd)
            else:
                path = self.path / name
                if is_link(path.lstat()):
                    return None
                fd = os.open(path, _READ_FLAGS)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as error:
            if error.errno == errno.ELOOP:
                return None
            raise
        with os.fdopen(fd, "rb") as file:
            return file.read()

    def unlink(self, name: str) -> None:
        if self._fd is not None:
            os.unlink(name, dir_fd=self._fd)
        else:
            (self.path / name).unlink()

    def rename(self, name: str, new_name: str, into: Dir | None = None) -> None:
        """Move ``name`` to ``new_name`` here, or into another handle."""
        target = self if into is None else into
        if self._fd is not None and target._fd is not None:
            os.rename(name, new_name, src_dir_fd=self._fd, dst_dir_fd=target._fd)
        else:
            (self.path / name).rename(target.path / new_name)

    def entries(self) -> list[tuple[str, bool]]:
        """``(name, is_dir)`` for every entry; symlinks are never dirs.

        Only for maintenance (pruning, the pytest layout), never on the hot
        path of making items.
        """
        scan = os.scandir(self._fd if self._fd is not None else self.path)
        with scan as it:
            return [(e.name, e.is_dir(follow_symlinks=False)) for e in it]

    def rmtree(self, name: str) -> str | None:
        """Delete ``name`` and everything in it; return why not, if it failed."""
        failures: list[str] = []

        def handle(func: Callable[..., Any], path: str, error: BaseException) -> None:
            # read-only entries (Windows, or chmod by a test) are made
            # writable once and retried
            if isinstance(error, PermissionError):
                try:
                    Path(path).chmod(stat.S_IRWXU)
                    Path(path).parent.chmod(stat.S_IRWXU)
                    func(path)
                except OSError as again:
                    failures.append(f"{path}: {again}")
                else:
                    return
            elif not isinstance(error, FileNotFoundError):
                failures.append(f"{path}: {error}")

        kwargs: dict[str, Any] = {}
        if sys.version_info >= (3, 12):
            kwargs["onexc"] = handle
        else:
            kwargs["onerror"] = lambda f, p, info: handle(f, p, info[1])
        # by path, so the handler can retry with absolute paths; rmtree
        # itself never follows symlinks inside the tree on Linux and macOS
        shutil.rmtree(self.path / name, **kwargs)
        if failures:
            return failures[0]
        if self.is_dir(name):
            return "still present after removal"
        return None


def _open_dir(name: str, dir_fd: int) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            msg = "a symlink or not a directory"
            raise NotADirectoryHere(error.errno, msg, name) from None
        raise


def _check_dir(path: Path) -> None:
    st = path.lstat()
    if is_link(st) or not stat.S_ISDIR(st.st_mode):
        raise NotADirectoryHere(
            errno.ENOTDIR, "a symlink, a junction or not a directory", str(path)
        )
