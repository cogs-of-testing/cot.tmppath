"""The intended public API, as stubs.

Nothing here is built yet: every entry point raises ``NotImplementedError``.
The shapes exist so that ``testing/`` can state what the library is for
(``docs/goals.md``) against real names, and so the tests flip from xfail to
pass one by one as the core lands.
"""

from __future__ import annotations

import enum
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    # typing_extensions is only needed by the type checker: no runtime deps
    from typing_extensions import Self

_NOT_BUILT = "cot.tmppath: not built yet, see docs/goals.md"


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
        raise NotImplementedError(_NOT_BUILT)


class Layout(Protocol):
    """Where runs and items go below a root. Layouts are policies."""

    def run_name(self, root: Path) -> str: ...

    def item_name(self, run: Path, name: str) -> str: ...


class FlatLayout:
    """The default: ``{root}/{run}/{item}``, items side by side."""

    def run_name(self, root: Path) -> str:
        raise NotImplementedError(_NOT_BUILT)

    def item_name(self, run: Path, name: str) -> str:
        raise NotImplementedError(_NOT_BUILT)


class PytestLayout:
    """Example policy: pytest's ``pytest-{N}/{testname}{M}`` layout."""

    def run_name(self, root: Path) -> str:
        raise NotImplementedError(_NOT_BUILT)

    def item_name(self, run: Path, name: str) -> str:
        raise NotImplementedError(_NOT_BUILT)


class Run:
    """One invocation's folder. Several processes may join the same run."""

    path: Path
    id: str

    def item(self, name: str) -> Path:
        """Create a new folder for ``name`` directly in the run folder."""
        raise NotImplementedError(_NOT_BUILT)

    def process_folder(self, name: str) -> Path:
        """The folder of one process in this run, named by whoever manages
        the processes (for pytest-xdist, the controller names its workers).

        Unlike ``item``, the name is used exactly, and asking twice gives the
        same folder. This is what ``getbasetemp()`` returns under pytest, so
        ``getbasetemp().parent`` stays the run's shared folder.
        """
        raise NotImplementedError(_NOT_BUILT)

    def finish_item(self, path: Path, outcome: Outcome) -> None:
        """Report an item's whole outcome; retention decides its fate."""
        raise NotImplementedError(_NOT_BUILT)

    def place(self, destination: Path) -> AbstractContextManager[Path]:
        """Give a staging path; move it to ``destination`` atomically on
        success, discard it on error."""
        raise NotImplementedError(_NOT_BUILT)

    def close(self) -> PruneReport:
        """Release this process's hold on the run and apply retention."""
        raise NotImplementedError(_NOT_BUILT)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class Root:
    """The folder that holds one project's runs."""

    path: Path

    def __init__(
        self,
        path: Path,
        *,
        retention: Retention = _DEFAULT_RETENTION,
        layout: Layout | None = None,
    ) -> None:
        raise NotImplementedError(_NOT_BUILT)

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

        The root is ``{temproot}/cot.tmppath-{user}/{project}``, with the
        user as the uid on POSIX and the user name on Windows. The per-user
        folder is private and owner-checked, so another user cannot block or
        read a project's runs by creating its folder first. Touches nothing
        on disk until the first run starts.
        """
        raise NotImplementedError(_NOT_BUILT)

    def start_run(self) -> Run:
        """Create a new run folder, locked by this process."""
        raise NotImplementedError(_NOT_BUILT)

    def join_run(self, run_id: str) -> Run:
        """Join a run another process started, sharing its folder."""
        raise NotImplementedError(_NOT_BUILT)

    @classmethod
    def all_projects(cls, *, temproot: Path | None = None) -> tuple[Root, ...]:
        """Every project root of the current user under the default location."""
        raise NotImplementedError(_NOT_BUILT)

    def plan_prune(self, *, older_than: float | None = None) -> PrunePlan:
        """What a prune would remove, without removing anything.

        ``older_than`` (seconds) also selects runs unused for that long,
        whatever retention says. Live runs are never selected.
        """
        raise NotImplementedError(_NOT_BUILT)

    def prune(self) -> PruneReport:
        """Remove runs that retention no longer keeps and whose owners are
        gone: ``plan_prune().apply()``."""
        raise NotImplementedError(_NOT_BUILT)
