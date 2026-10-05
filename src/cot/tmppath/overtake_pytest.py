"""Opt-in: replace pytest's tmp fixtures with cot.tmppath.

Enable it per project with::

    [pytest]
    addopts = -p cot.tmppath.overtake_pytest

Loaded with ``-p``, it unregisters pytest's ``tmpdir`` plugin and provides
``tmp_path``, ``tmp_path_factory``, ``tmpdir`` and ``tmpdir_factory`` itself.
pytest's ``tmp_path_retention_count`` and ``tmp_path_retention_policy``
settings keep working, and pytest-xdist workers join the controller's run.
``--basetemp`` must name a folder that does not exist yet; it is created and
nothing under it is ever deleted. See docs/pytest-replacement.md.

This module imports pytest; the rest of cot.tmppath never does.
"""

from __future__ import annotations

import os
import re
import stat
import time
import warnings
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from _pytest.compat import legacy_path

from ._api import KEEP_EVERYTHING, Outcome, Retention, Root, Run

_RUN_ID = "cot_tmppath_run"
_ROOT = "cot_tmppath_root"
_PROCESS = "cot_tmppath_process"
_item_path = pytest.StashKey[Path]()
_item_failed = pytest.StashKey[bool]()
_state = pytest.StashKey["_State"]()


def pytest_addoption(parser: pytest.Parser, pluginmanager: Any) -> None:
    tmpdir = pluginmanager.get_plugin("tmpdir")
    if tmpdir is not None:
        # The tmpdir plugin already registered the retention ini options, so
        # existing configurations stay valid under --strict-config.
        pluginmanager.unregister(tmpdir)
    else:
        # Blocked by the user with -p no:tmpdir: its ini options are missing.
        parser.addini(
            "tmp_path_retention_count",
            help="How many runs to keep (cot.tmppath).",
            default="3",
        )
        parser.addini(
            "tmp_path_retention_policy",
            help="Which item folders to keep: all/failed/none (cot.tmppath).",
            default="all",
        )
    pluginmanager.set_blocked("tmpdir")


def _retention(config: pytest.Config) -> Retention:
    count = int(config.getini("tmp_path_retention_count"))
    policy = config.getini("tmp_path_retention_policy")
    if count < 0 or policy not in ("all", "failed", "none"):
        msg = (
            "tmp_path_retention_count must be >= 0 and tmp_path_retention_policy"
            f" all, failed or none; got {count!r} and {policy!r}"
        )
        raise pytest.UsageError(msg)
    return Retention(
        keep_failed_only=policy == "failed",
        keep_runs=0 if policy == "none" else count,
    )


class _State:
    """The run, started or joined on first use, never at configure time."""

    def __init__(self, config: pytest.Config) -> None:
        self._config = config
        self._run: Run | None = None

    def _root(self) -> Root:
        retention = _retention(self._config)
        worker = getattr(self._config, "workerinput", None)
        if worker is not None:
            return Root(Path(worker[_ROOT]), retention=retention)
        basetemp = self._config.option.basetemp
        if basetemp:
            # a folder the user named is never pruned, whatever retention says
            return Root(Path(basetemp), retention=KEEP_EVERYTHING)
        temproot = os.environ.get("PYTEST_DEBUG_TEMPROOT")
        return Root.for_project(
            self._config.rootpath.name,
            temproot=Path(temproot) if temproot else None,
            retention=retention,
        )

    @property
    def process(self) -> str:
        worker = getattr(self._config, "workerinput", None)
        return str(worker[_PROCESS]) if worker is not None else "main"

    @property
    def run(self) -> Run:
        if self._run is None:
            root = self._root()
            worker = getattr(self._config, "workerinput", None)
            if worker is not None:
                self._run = root.join_run(worker[_RUN_ID])
            else:
                self._run = root.start_run()
            self._config.add_cleanup(self._close)
        return self._run

    def _close(self) -> None:
        assert self._run is not None
        for path, reason in self._run.close().failed:
            # reported, never swallowed (docs/goals.md, G4)
            warnings.warn(
                pytest.PytestWarning(f"cot.tmppath could not remove {path}: {reason}"),
                stacklevel=1,
            )


# An empty folder this young was made by whoever started pytest, for this
# run, the way pytester's runpytest_subprocess does it.
_FRESH_SECONDS = 10.0


def _check_basetemp(config: pytest.Config) -> None:
    basetemp = config.option.basetemp
    # xdist hands workers the controller's options, by then the folder exists
    if not basetemp or hasattr(config, "workerinput"):
        return
    path = Path(basetemp)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    # lstat, never stat: a symlink to a fresh empty folder is not fresh
    owned = not hasattr(os, "getuid") or info.st_uid == os.getuid()
    fresh = (
        stat.S_ISDIR(info.st_mode)
        and owned
        and time.time() - info.st_mtime < _FRESH_SECONDS
        and not any(path.iterdir())
    )
    if fresh:
        config.issue_config_time_warning(
            pytest.PytestWarning(
                f"--basetemp={basetemp} already exists; using it because it is"
                f" empty and less than {_FRESH_SECONDS:.0f}s old. Pass a path"
                " that does not exist yet."
            ),
            stacklevel=2,
        )
        return
    msg = (
        f"--basetemp={basetemp} already exists. cot.tmppath only creates a"
        " new folder there and never deletes one; remove it yourself or"
        " name a path that does not exist yet."
    )
    raise pytest.UsageError(msg)


def pytest_configure(config: pytest.Config) -> None:
    _check_basetemp(config)
    config.stash[_state] = _State(config)


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Any) -> None:
    run = node.config.stash[_state].run
    node.workerinput[_ROOT] = str(run.path.parent)
    node.workerinput[_RUN_ID] = run.id
    # the manager names each process's folder, workers never pick their own
    node.workerinput[_PROCESS] = node.gateway.id


class TempPathFactory:
    """What ``tmp_path_factory`` returns.

    It offers the part of pytest's factory that pytester, plugins and
    conftests use: ``mktemp`` and ``getbasetemp``.
    """

    def __init__(self, state: _State) -> None:
        self._state = state

    def getbasetemp(self) -> Path:
        # per process, so getbasetemp().parent is the run's shared folder,
        # as it is for pytest under xdist
        return self._state.run.process_folder(self._state.process)

    def mktemp(self, basename: str, numbered: bool = True) -> Path:
        # Every item gets a unique name; numbered=False cannot promise the
        # exact name in a run that other processes share.
        return self._state.run.item(basename)


@pytest.fixture(scope="session")
def tmp_path_factory(request: pytest.FixtureRequest) -> TempPathFactory:
    return TempPathFactory(request.config.stash[_state])


class TempdirFactory:
    """What ``tmpdir_factory`` returns: the same, with ``py.path`` results."""

    def __init__(self, factory: TempPathFactory) -> None:
        self._factory = factory

    def getbasetemp(self) -> Any:
        return legacy_path(self._factory.getbasetemp())

    def mktemp(self, basename: str, numbered: bool = True) -> Any:
        return legacy_path(self._factory.mktemp(basename, numbered))


@pytest.fixture(scope="session")
def tmpdir_factory(tmp_path_factory: TempPathFactory) -> TempdirFactory:
    return TempdirFactory(tmp_path_factory)


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest, tmp_path_factory: TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp(re.sub(r"\W", "_", request.node.name))
    request.node.stash[_item_path] = path
    return path


@pytest.fixture
def tmpdir(tmp_path: Path) -> Any:
    return legacy_path(tmp_path)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    failed = item.stash.get(_item_failed, False) or report.failed
    item.stash[_item_failed] = failed
    # The fixture's own teardown runs before this report, so only here is
    # the whole outcome (setup, call and teardown) known.
    if report.when == "teardown" and _item_path in item.stash:
        outcome = Outcome.FAILED if failed else Outcome.PASSED
        item.config.stash[_state].run.finish_item(item.stash[_item_path], outcome)
    return report
