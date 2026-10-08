"""Opt-in: replace pytest's tmp fixtures with cot.tmppath.

Enable it per project with::

    [pytest]
    addopts = -p cot.tmppath.overtake_pytest

Loaded with ``-p``, it unregisters pytest's ``tmpdir`` plugin and provides
``tmp_path`` and ``tmp_path_factory`` itself; a later
``-p no:cot.tmppath.overtake_pytest`` opts out again and leaves pytest's own
fixtures in place. Loading it from a conftest's ``pytest_plugins`` is too late
and is refused with a usage error. The ``py.path`` fixtures
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
_taken_over = pytest.StashKey[bool]()


def pytest_addoption(parser: pytest.Parser, pluginmanager: Any) -> None:
    # Nothing is taken over here: this runs as soon as the module is
    # registered, while the command line is still being read, and a later
    # ``-p no:cot.tmppath.overtake_pytest`` unregisters the module again.
    if pluginmanager.get_plugin("tmpdir") is None:
        # Blocked by the user with -p no:tmpdir: its ini options are missing,
        # and existing configurations must stay valid under --strict-config.
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


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config: pytest.Config) -> None:
    # The first hook after every -p, PYTEST_ADDOPTS, addopts and entry point
    # has been processed: a module still registered now stays registered.
    # Before any pytest_configure, so the tmpdir plugin never configures
    # itself and legacypath skips tmpdir and tmpdir_factory. The ini options
    # it added stay, so configurations stay valid under --strict-config.
    pluginmanager = early_config.pluginmanager
    tmpdir = pluginmanager.get_plugin("tmpdir")
    if tmpdir is not None:
        pluginmanager.unregister(tmpdir)
    pluginmanager.set_blocked("tmpdir")
    early_config.stash[_taken_over] = True


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
    if not config.stash.get(_taken_over, False):
        # Loaded after the hook above, from a conftest's pytest_plugins: the
        # tmpdir plugin is configuring itself in this very hook call, and
        # would replace config._tmp_path_factory after this.
        msg = f"enable {__name__} with -p {__name__}, not from a conftest"
        raise pytest.UsageError(msg)
    state = config.stash[_state] = _State(config)
    _check_basetemp(config)
    # what pytest's own tmpdir plugin sets: pytest-xdist and other plugins
    # look for it, and get the cot.tmppath factory
    config._tmp_path_factory = TempPathFactory(state)  # type: ignore[attr-defined]
    # The fixtures are registered only now. pytest parses the fixtures of
    # every plugin that was ever registered, even one a later
    # -p no:cot.tmppath.overtake_pytest unregistered again, so defining them
    # at module level would shadow pytest's own after an opt-out.
    config.pluginmanager.register(_Fixtures(), f"{__name__}.fixtures")


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


class _Fixtures:
    """``tmp_path``, ``tmp_path_factory`` and the outcome they are kept by."""

    @pytest.fixture(scope="session")
    def tmp_path_factory(self, request: pytest.FixtureRequest) -> TempPathFactory:
        factory: TempPathFactory = request.config._tmp_path_factory  # type: ignore[attr-defined]
        return factory

    @pytest.fixture
    def tmp_path(
        self, request: pytest.FixtureRequest, tmp_path_factory: TempPathFactory
    ) -> Path:
        path = tmp_path_factory.mktemp(re.sub(r"\W", "_", request.node.name))
        request.node.stash[_item_path] = path
        return path

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[None]
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
