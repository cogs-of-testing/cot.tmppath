"""Opt-in: replace pytest's tmp fixtures with cot.tmppath.

Enable it per project with::

    [pytest]
    addopts = -p cot.tmppath.overtake_pytest

Loaded with ``-p``, it unregisters pytest's ``tmpdir`` plugin and provides
``tmp_path``, ``tmp_path_factory``, ``tmpdir`` and ``tmpdir_factory`` itself.
pytest's ``tmp_path_retention_count`` and ``tmp_path_retention_policy``
settings and ``--basetemp`` keep working, and pytest-xdist workers join the
controller's run. See docs/pytest-replacement.md.

This module imports pytest; the rest of cot.tmppath never does.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from _pytest.compat import legacy_path

from ._api import Outcome, Retention, Root, Run

_RUN_ID = "cot_tmppath_run"
_ROOT = "cot_tmppath_root"
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
            return Root(Path(basetemp), retention=retention)
        return Root.for_project(self._config.rootpath.name, retention=retention)

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


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_state] = _State(config)


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Any) -> None:
    run = node.config.stash[_state].run
    node.workerinput[_ROOT] = str(run.path.parent)
    node.workerinput[_RUN_ID] = run.id


class TempPathFactory:
    """What ``tmp_path_factory`` and ``tmpdir_factory`` return.

    It offers the part of pytest's factory that pytester, plugins and
    conftests use: ``mktemp`` and ``getbasetemp``.
    """

    def __init__(self, state: _State) -> None:
        self._state = state

    def getbasetemp(self) -> Path:
        return self._state.run.path

    def mktemp(self, basename: str, numbered: bool = True) -> Path:
        # Every item gets a unique name; numbered=False cannot promise the
        # exact name in a run that other processes share.
        return self._state.run.item(basename)


@pytest.fixture(scope="session")
def tmp_path_factory(request: pytest.FixtureRequest) -> TempPathFactory:
    return TempPathFactory(request.config.stash[_state])


@pytest.fixture(scope="session")
def tmpdir_factory(tmp_path_factory: TempPathFactory) -> TempPathFactory:
    return tmp_path_factory


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
