"""Opt-in: replace pytest's tmp fixtures with cot.tmppath.

Enable it per project with::

    [pytest]
    addopts = -p cot.tmppath.overtake_pytest

Loaded with ``-p``, it unregisters pytest's ``tmpdir`` plugin and provides
``tmp_path`` and ``tmp_path_factory`` itself. The ``py.path`` fixtures
``tmpdir`` and ``tmpdir_factory`` are left out on purpose: with the plugin
on, a test that asks for them fails with "fixture not found".
It also sets ``config._tmp_path_factory``, so pytest's basetemp handling is
replaced as a whole. pytest's ``tmp_path_retention_count`` and
``tmp_path_retention_policy`` settings keep working.

Layout: ``getbasetemp()`` is the run folder, and ``tmp_path`` folders are
made inside it. An xdist worker's ``getbasetemp()`` is its own folder in the
controller's run. ``--basetemp`` names the root that holds the runs; it is
used only if it is missing, empty, or made by cot.tmppath, and only folders
cot.tmppath made there are ever removed. See docs/pytest-replacement.md.

This module imports pytest; the rest of cot.tmppath never does.
"""

from __future__ import annotations

import os
import re
import warnings
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from ._api import Outcome, Retention, Root, Run, UnsafeRootError

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
        self._root: Root | None = None
        self._run: Run | None = None

    @property
    def root(self) -> Root:
        if self._root is None:
            self._root = self._make_root()
        return self._root

    def _make_root(self) -> Root:
        retention = _retention(self._config)
        worker = getattr(self._config, "workerinput", None)
        if worker is not None:
            return Root(Path(worker[_ROOT]), retention=retention)
        basetemp = self._config.option.basetemp
        if basetemp:
            # a root like any other: runs go inside it, and only folders
            # cot.tmppath made there are ever removed
            return Root(Path(basetemp), retention=retention)
        temproot = os.environ.get("PYTEST_DEBUG_TEMPROOT")
        return Root.for_project(
            self._config.rootpath.name,
            temproot=Path(temproot) if temproot else None,
            retention=retention,
        )

    @property
    def process(self) -> str | None:
        """The xdist worker's folder name; None for the controller and
        for a run without xdist, whose folders go in the run itself."""
        worker = getattr(self._config, "workerinput", None)
        return str(worker[_PROCESS]) if worker is not None else None

    @property
    def run(self) -> Run:
        if self._run is None:
            root = self.root
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


def _check_basetemp(config: pytest.Config) -> None:
    # xdist hands workers the controller's options; the controller checked
    if not config.option.basetemp or hasattr(config, "workerinput"):
        return
    try:
        config.stash[_state].root.ensure()
    except UnsafeRootError as error:
        msg = f"--basetemp={config.option.basetemp}: {error.strerror}"
        raise pytest.UsageError(msg) from None


def pytest_configure(config: pytest.Config) -> None:
    state = config.stash[_state] = _State(config)
    _check_basetemp(config)
    # what pytest's own tmpdir plugin sets: pytest-xdist and other plugins
    # look for it, and get the cot.tmppath factory
    config._tmp_path_factory = TempPathFactory(state)  # type: ignore[attr-defined]


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
        # the run; in an xdist worker its own folder in the run, so
        # getbasetemp().parent is the run there, as it is for pytest
        run, process = self._state.run, self._state.process
        return run.path if process is None else run.process_folder(process)

    def mktemp(self, basename: str, numbered: bool = True) -> Path:
        # Inside getbasetemp(), as pytest does. Every item gets a unique
        # name; numbered=False cannot promise the exact name.
        return self._state.run.item(basename, process=self._state.process)


@pytest.fixture(scope="session")
def tmp_path_factory(request: pytest.FixtureRequest) -> TempPathFactory:
    factory: TempPathFactory = request.config._tmp_path_factory  # type: ignore[attr-defined]
    return factory


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest, tmp_path_factory: TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp(re.sub(r"\W", "_", request.node.name))
    request.node.stash[_item_path] = path
    return path


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
