"""The public API: roots, runs, items, retention (docs/goals.md).

On disk a root holds a marker file and one folder per run. A run holds its
items side by side, a ``.cot-run`` file with its start time, a
``.cot-holders`` folder with one file per process using it, and a trash
folder per process. Every name starting with ``.cot-`` is the library's.
"""

from __future__ import annotations

import enum
import errno
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ._fs import Dir, NotADirectoryHere
from ._owner import Owner

if TYPE_CHECKING:
    # typing_extensions is only needed by the type checker: no runtime deps
    from typing_extensions import Self

_ROOT_MARKER = ".cot-tmppath"
_RUN_MARKER = ".cot-run"
_HOLDERS = ".cot-holders"
_TRASH = ".cot-trash-"
_PLACE = ".cot-place-"
_DELETING = ".cot-deleting-"
_RESERVED = ".cot-"


class UnsafeRootError(OSError):
    """A root, or a folder at a shared level, is not safe to use.

    Raised for a symlink, a folder owned by another user, permissions
    others can use, or an existing folder this library did not create.
    """


class Outcome(enum.Enum):
    """What a host reports about an item when it is done with it."""

    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True)
class Retention:
    """Which runs and items survive.

    ``keep_failed_only`` removes an item's folder when its whole outcome
    passed. ``keep_runs`` and ``max_age`` bound the runs kept per root.
    ``KEEP_EVERYTHING`` deletes nothing at all.
    """

    keep_failed_only: bool = False
    keep_runs: int | None = 3  # None: no limit
    max_age: float | None = None


_DEFAULT_RETENTION = Retention()
KEEP_EVERYTHING = Retention(keep_failed_only=False, keep_runs=None)


@dataclass(frozen=True)
class PruneReport:
    """What a prune removed, and what it could not remove, with why."""

    removed: tuple[Path, ...] = ()
    failed: tuple[tuple[Path, str], ...] = field(default=())


@dataclass(frozen=True)
class PrunePlan:
    """Runs a prune selected, to show before anything is removed."""

    paths: tuple[Path, ...] = ()

    def apply(self) -> PruneReport:
        """Remove exactly the planned paths that are still safe to remove.

        Each one is checked again (marker, owner, owner dead), so a run that
        came alive since the plan was made is skipped and reported.
        """
        removed: list[Path] = []
        failed: list[tuple[Path, str]] = []
        for path in self.paths:
            try:
                reason = _remove_run(path)
            except OSError as error:
                reason = str(error)
            if reason is None:
                removed.append(path)
            elif reason:
                failed.append((path, reason))
        return PruneReport(tuple(removed), tuple(failed))


def _remove_run(path: Path) -> str | None:
    """Remove one run; ``None`` if removed, ``""`` if already gone, else why not."""
    with Dir.open(path.parent) as root:
        if root.read_file(_ROOT_MARKER) is None:
            return "not in a cot.tmppath root"
        name = path.name
        if not name.startswith(_DELETING):
            try:
                run = root.open_dir(name)
            except FileNotFoundError:
                return ""
            except NotADirectoryHere:
                return "a symlink or not a directory"
            with run:
                if run.read_file(_RUN_MARKER) is None:
                    return "not a cot.tmppath run"
                if _is_live(run):
                    return "in use again"
            gone = f"{_DELETING}{name}-{secrets.token_hex(3)}"
            try:
                # out of its place at once; the slow part happens after
                root.rename(name, gone)
            except FileNotFoundError:
                return ""
            name = gone
        return root.rmtree(name)


def _is_live(run: Dir) -> bool:
    """Whether any process holding the run may still be alive."""
    try:
        holders = run.open_dir(_HOLDERS)
    except FileNotFoundError:
        return False
    with holders:
        for name, _ in holders.entries():
            data = holders.read_file(name)
            owner = None if data is None else Owner.from_bytes(data)
            # a holder file being written right now reads empty: alive
            if owner is None or owner.is_alive():
                return True
    return False


def _check_name(name: str, what: str) -> str:
    if (
        not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or "\0" in name
        or name.startswith(_RESERVED)
    ):
        msg = f"{what} {name!r} is not a plain folder name"
        raise ValueError(msg)
    return name


class Layout(Protocol):
    """Where runs and items go below a root. Layouts are policies.

    Each method returns a candidate name. When the name is already taken,
    the caller asks again, so a layout hands out a different name on the
    next call for the same arguments.
    """

    def run_name(self, root: Path) -> str: ...

    def item_name(self, run: Path, name: str) -> str: ...


_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")
_WINDOWS_RESERVED = re.compile(r"(?i)(con|prn|aux|nul|com\d|lpt\d)(\..*)?")
_MAX_ITEM = 56


def _readable(name: str) -> str:
    """``name`` as a short, portable folder name, the same in every run."""
    readable = _UNSAFE_CHARS.sub("_", name).strip("._") or "item"
    if _WINDOWS_RESERVED.fullmatch(readable):
        readable = "_" + readable
    if len(readable) > _MAX_ITEM:
        digest = hashlib.sha256(name.encode("utf-8", "surrogatepass")).hexdigest()
        readable = f"{readable[: _MAX_ITEM - 9]}-{digest[:8]}"
    return readable


class FlatLayout:
    """The default: ``{root}/{prefix}-{stamp}/{item}``, items side by side.

    A run is named ``{prefix}-{YYYYMMDD-HHMMSS}-{random}``; the prefix is
    the project for a default root and ``run`` otherwise. An item is named
    after what the caller names it; a second item of the same name gets
    ``-1``, ``-2``, ... appended. The counters live in this object, so
    making an item never lists the run folder (G3).
    """

    def __init__(self, prefix: str = "run") -> None:
        self._prefix = _check_name(prefix, "run prefix")
        self._next: dict[tuple[Path, str], int] = {}

    def run_name(self, root: Path) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return f"{self._prefix}-{stamp}-{secrets.token_hex(3)}"

    def item_name(self, run: Path, name: str) -> str:
        base = _readable(name)
        key = (run, base)
        count = self._next.get(key, 0)
        self._next[key] = count + 1
        return base if count == 0 else f"{base}-{count}"


class PytestLayout:
    """Example policy: pytest's ``pytest-{N}/{testname}{M}`` layout.

    Like pytest it lists the folder to find the next number, so it is
    slower than the flat layout with many items.
    """

    def run_name(self, root: Path) -> str:
        return f"pytest-{_next_number(root, 'pytest-')}"

    def item_name(self, run: Path, name: str) -> str:
        base = re.sub(r"\W", "_", name)[:30]
        return f"{base}{_next_number(run, base)}"


def _next_number(folder: Path, prefix: str) -> int:
    pattern = re.compile(re.escape(prefix) + r"(\d+)")
    numbers = [-1]
    with suppress(FileNotFoundError), os.scandir(folder) as entries:
        for entry in entries:
            match = pattern.fullmatch(entry.name)
            if match:
                numbers.append(int(match[1]))
    return max(numbers) + 1


_last_start = 0


def _start_ns() -> int:
    # strictly increasing within a process, so runs started in a quick
    # loop keep their order even with a coarse clock (Windows)
    global _last_start
    _last_start = max(time.time_ns(), _last_start + 1)
    return _last_start


class Run:
    """One invocation's folder. Several processes may join the same run."""

    path: Path
    id: str

    def __init__(self, root: Root, folder: Dir, run_id: str) -> None:
        self.path = folder.path
        self.id = run_id
        self._root = root
        self._dir = folder
        self._token = f"{os.getpid()}-{secrets.token_hex(4)}"
        self._trash: Dir | None = None
        self._processes: dict[str, Dir] = {}
        self._closed = False

    def _hold(self) -> None:
        with suppress(FileExistsError):
            self._dir.mkdir(_HOLDERS)
        with self._dir.open_dir(_HOLDERS) as holders:
            holders.create_file(self._token, Owner.current().to_bytes())

    def _check_open(self) -> None:
        if self._closed:
            msg = f"run {self.path} is closed"
            raise ValueError(msg)

    def item(self, name: str, *, process: str | None = None) -> Path:
        """Create a new folder for ``name`` in the run folder, or inside
        the process folder ``process`` when one is named."""
        self._check_open()
        folder = self._dir if process is None else self._process_dir(process)
        layout = self._root.layout
        for _ in range(100_000):
            candidate = _check_name(layout.item_name(folder.path, name), "item")
            try:
                return folder.mkdir(candidate)
            except FileExistsError:
                continue
        msg = f"no free item name for {name!r} in {folder.path}"
        raise FileExistsError(errno.EEXIST, msg)

    def _process_dir(self, name: str) -> Dir:
        if name not in self._processes:
            self.process_folder(name)
            self._processes[name] = self._dir.open_dir(name)
        return self._processes[name]

    def process_folder(self, name: str) -> Path:
        """The folder of one process in this run, named by whoever manages
        the processes (for pytest-xdist, the controller names its workers).

        Unlike ``item``, the name is used exactly, and asking twice gives the
        same folder. This is what ``getbasetemp()`` returns under pytest, so
        ``getbasetemp().parent`` stays the run's shared folder.
        """
        self._check_open()
        _check_name(name, "process folder")
        try:
            return self._dir.mkdir(name)
        except FileExistsError:
            if not self._dir.is_dir(name):
                msg = f"{self.path / name} exists and is not a folder"
                raise UnsafeRootError(errno.EEXIST, msg) from None
            return self.path / name

    def finish_item(self, path: Path, outcome: Outcome) -> None:
        """Report an item's whole outcome; retention decides its fate.

        A removed item is gone from its place at once: it is moved to this
        process's trash, which is emptied when the run is closed.
        """
        self._check_open()
        path = Path(path)
        if path.parent == self.path:
            folder = self._dir
        elif path.parent.parent == self.path and path.parent.name in self._processes:
            folder = self._processes[path.parent.name]
        else:
            msg = f"{path} is not an item of run {self.path}"
            raise ValueError(msg)
        if outcome is Outcome.FAILED or not self._root.retention.keep_failed_only:
            return
        if self._trash is None:
            trash = _TRASH + self._token
            with suppress(FileExistsError):
                self._dir.mkdir(trash)
            self._trash = self._dir.open_dir(trash)
        with suppress(FileNotFoundError):
            folder.rename(path.name, secrets.token_hex(8), into=self._trash)

    @contextmanager
    def place(self, destination: Path) -> Iterator[Path]:
        """Give a staging path; move it to ``destination`` atomically on
        success, discard it on error."""
        self._check_open()
        destination = Path(destination)
        staging_name = _PLACE + secrets.token_hex(6)
        staging = self._dir.mkdir(staging_name) / destination.name
        try:
            yield staging
            _move(staging, destination)
        finally:
            self._dir.rmtree(staging_name)

    def close(self) -> PruneReport:
        """Release this process's hold on the run and apply retention.

        Closing twice does nothing the second time.
        """
        if self._closed:
            return PruneReport()
        self._closed = True
        failed: list[tuple[Path, str]] = []
        try:
            with self._dir.open_dir(_HOLDERS) as holders:
                holders.unlink(self._token)
        except FileNotFoundError:
            pass
        for process in self._processes.values():
            process.close()
        self._processes.clear()
        if self._trash is not None:
            self._trash.close()
            reason = self._dir.rmtree(_TRASH + self._token)
            if reason:
                failed.append((self.path / (_TRASH + self._token), reason))
        self._dir.close()
        report = self._root.prune()
        return PruneReport(report.removed, (*failed, *report.failed))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _move(staging: Path, destination: Path) -> None:
    try:
        staging.replace(destination)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        # another filesystem: copy next to the destination, then rename
        temporary = destination.with_name(
            f".{destination.name}.cot-tmppath-{secrets.token_hex(4)}"
        )
        try:
            if staging.is_dir():
                shutil.copytree(staging, temporary, symlinks=True)
            else:
                shutil.copy2(staging, temporary)
            temporary.replace(destination)
        except BaseException:
            if temporary.is_dir():
                shutil.rmtree(temporary, ignore_errors=True)
            else:
                with suppress(FileNotFoundError):
                    temporary.unlink()
            raise


def _user_folder_name() -> str:
    if hasattr(os, "getuid"):
        return f"cot-{os.getuid()}"
    return f"cot-{_UNSAFE_CHARS.sub('_', getpass.getuser())}"


def _unsafe(path: Path, problem: str, fix: str) -> UnsafeRootError:
    return UnsafeRootError(errno.EPERM, f"{path} {problem}; {fix}")


def _secure_child(parent: Dir, name: str, *, create: bool) -> Dir | None:
    """Open (and with ``create``, make) one checked level below ``parent``.

    The folder must be a real folder, owned by this user, private, and
    carry the marker. An empty folder of this user without the marker is
    adopted; anything else is refused, never wiped.
    """
    path = parent.path / name
    created = False
    if create:
        try:
            parent.mkdir(name)
            created = True
        except FileExistsError:
            pass
    try:
        folder = parent.open_dir(name)
    except FileNotFoundError:
        if create:
            raise
        return None
    except NotADirectoryHere:
        raise _unsafe(
            path, "is a symlink or not a folder", "remove it or name another folder"
        ) from None
    try:
        if not created:
            _check_owner(folder)
        if folder.read_file(_ROOT_MARKER) is None:
            if not created:
                if not create or folder.entries():
                    raise _unsafe(
                        path,
                        "exists and was not created by cot.tmppath",
                        "nothing in it was touched; remove it or name another folder",
                    )
                # empty and ours: made for us just now (pytester's --basetemp)
                if hasattr(os, "getuid"):
                    folder.chmod(0o700)
            folder.create_file(_ROOT_MARKER, b"cot.tmppath\n")
        elif not created:
            _check_private(folder)
        return folder
    except BaseException:
        folder.close()
        raise


def _check_owner(folder: Dir) -> None:
    if hasattr(os, "getuid") and folder.stat().st_uid != os.getuid():
        raise _unsafe(folder.path, "is owned by another user", "name a folder you own")


def _check_private(folder: Dir) -> None:
    if hasattr(os, "getuid") and stat.S_IMODE(folder.stat().st_mode) & 0o077:
        raise _unsafe(
            folder.path,
            "has permissions other users can use",
            f"run chmod 700 {folder.path} if that is expected",
        )


class Root:
    """The folder that holds one project's runs.

    A default root (``for_project``) shares the per-user folder with the
    user's other projects; its runs are told apart by the project recorded
    in each run's marker, so retention only ever counts a project's own runs.
    """

    path: Path

    def __init__(
        self,
        path: Path,
        *,
        retention: Retention = _DEFAULT_RETENTION,
        layout: Layout | None = None,
    ) -> None:
        # absolute, never resolved: a symlink at the root must stay visible
        path = Path(path).absolute()
        self._init(path.parent, path.name, None, retention, layout)

    def _init(
        self,
        base: Path,
        name: str,
        project: str | None,
        retention: Retention,
        layout: Layout | None,
    ) -> None:
        self._base = base
        self._name = name
        self.project = project
        self.path = base / name
        self.retention = retention
        if layout is None:
            layout = FlatLayout("run" if project is None else project)
        self.layout: Layout = layout

    @classmethod
    def for_project(
        cls,
        project: str,
        *,
        temproot: Path | None = None,
        retention: Retention = _DEFAULT_RETENTION,
        layout: Layout | None = None,
    ) -> Root:
        """The default root for ``project`` under the system temp folder.

        The root is the per-user folder ``{temproot}/cot-{user}``, with the
        user as the uid on POSIX and the user name on Windows, and the runs
        are named ``{project}-{stamp}``, so an item sits two levels below
        the temp folder, as with pytest. The per-user folder is private and
        owner-checked, so another user cannot block or read a project's runs
        by creating it first. Touches nothing on disk until the first run
        starts.
        """
        _check_name(project, "project")
        base = Path(tempfile.gettempdir() if temproot is None else temproot)
        root = cls.__new__(cls)
        root._init(base.absolute(), _user_folder_name(), project, retention, layout)
        return root

    def __repr__(self) -> str:
        return f"Root({str(self.path)!r})"

    def _open(self, *, create: bool) -> Dir | None:
        if create:
            self._base.mkdir(parents=True, exist_ok=True)
        elif not self._base.is_dir():
            return None
        with Dir.at(self._base) as base:
            return _secure_child(base, self._name, create=create)

    def ensure(self) -> None:
        """Create the root, or check an existing one, without starting a run.

        Raises ``UnsafeRootError`` for anything the root may not use: a
        symlink, another user's folder, or a non-empty folder cot.tmppath
        did not create. Nothing in such a folder is touched.
        """
        folder = self._open(create=True)
        assert folder is not None
        folder.close()

    def start_run(self) -> Run:
        """Create a new run folder, held by this process."""
        root = self._open(create=True)
        assert root is not None
        with root:
            for _ in range(1000):
                name = _check_name(self.layout.run_name(self.path), "run")
                try:
                    root.mkdir(name)
                    break
                except FileExistsError:
                    continue
            else:
                msg = f"no free run name in {self.path}"
                raise FileExistsError(errno.EEXIST, msg)
            run = Run(self, root.open_dir(name), name)
        # held before it is marked as a run, so a prune never sees it unheld
        run._hold()
        marker: dict[str, object] = {"start_ns": _start_ns()}
        if self.project is not None:
            marker["project"] = self.project
        start = json.dumps(marker).encode()
        run._dir.create_file(_RUN_MARKER, start)
        return run

    def join_run(self, run_id: str) -> Run:
        """Join a run another process started, sharing its folder."""
        _check_name(run_id, "run id")
        root = self._open(create=False)
        if root is None:
            msg = f"no cot.tmppath root at {self.path}"
            raise FileNotFoundError(errno.ENOENT, msg)
        with root:
            folder = root.open_dir(run_id)
        if folder.read_file(_RUN_MARKER) is None:
            folder.close()
            msg = f"{folder.path} is not a cot.tmppath run"
            raise FileNotFoundError(errno.ENOENT, msg)
        run = Run(self, folder, run_id)
        run._hold()
        return run

    @classmethod
    def all_projects(
        cls,
        *,
        temproot: Path | None = None,
        retention: Retention = _DEFAULT_RETENTION,
    ) -> tuple[Root, ...]:
        """Every project root of the current user under the default location,
        found from the projects recorded in its runs, each with
        ``retention``."""
        base = Path(tempfile.gettempdir() if temproot is None else temproot)
        if not base.is_dir():
            return ()
        with Dir.at(base) as folder:
            user = _secure_child(folder, _user_folder_name(), create=False)
        if user is None:
            return ()
        projects: set[str] = set()
        with user:
            for name, is_dir in user.entries():
                if not is_dir or name.startswith(_DELETING):
                    continue
                try:
                    run = user.open_dir(name)
                except (FileNotFoundError, NotADirectoryHere):
                    continue
                with run:
                    marker = _read_marker(run)
                if marker is not None and marker[1] is not None:
                    projects.add(marker[1])
        return tuple(
            cls.for_project(p, temproot=base, retention=retention)
            for p in sorted(projects)
        )

    def plan_prune(self, *, older_than: float | None = None) -> PrunePlan:
        """What a prune would remove, without removing anything.

        ``older_than`` (seconds) also selects runs unused for that long,
        whatever retention says. Live runs are never selected.
        """
        root = self._open(create=False)
        if root is None:
            return PrunePlan()
        stale: list[Path] = []
        runs: list[tuple[int, str, bool, float]] = []
        with root:
            for name, is_dir in root.entries():
                if not is_dir:
                    continue
                if name.startswith(_DELETING):
                    # a removal that was interrupted
                    stale.append(self.path / name)
                    continue
                try:
                    run = root.open_dir(name)
                except (FileNotFoundError, NotADirectoryHere):
                    continue
                with run:
                    marker = _read_marker(run)
                    # a shared user folder holds other projects' runs too
                    if marker is None or marker[1] != self.project:
                        continue
                    runs.append((marker[0], name, _is_live(run), root.mtime(name)))
        runs.sort(reverse=True)
        now = time.time()
        keep_runs, max_age = self.retention.keep_runs, self.retention.max_age
        selected = [
            self.path / name
            for index, (start, name, live, used) in enumerate(runs)
            if not live
            and (
                (keep_runs is not None and index >= keep_runs)
                or (max_age is not None and now - start / 1e9 > max_age)
                or (older_than is not None and now - used >= older_than)
            )
        ]
        return PrunePlan((*stale, *selected))

    def prune(self) -> PruneReport:
        """Remove runs that retention no longer keeps and whose owners are
        gone: ``plan_prune().apply()``."""
        return self.plan_prune().apply()


def _read_marker(run: Dir) -> tuple[int, str | None] | None:
    """A run's start time and project, or None if it is not a run."""
    data = run.read_file(_RUN_MARKER)
    if data is None:
        return None
    try:
        marker = json.loads(data)
        project = marker.get("project")
        return int(marker["start_ns"]), project if isinstance(project, str) else None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
