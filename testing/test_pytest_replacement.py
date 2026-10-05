"""pytest can run with its tmp fixtures replaced by a plain plugin.

cot.tmppath ships no pytest binding (docs/pytest-replacement.md). These
tests pin the pytest-side mechanism with a stand-in factory, independent of
the core, so a pytest or xdist release that breaks it fails here.
"""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]

STAND_IN = """
import itertools
import re
from pathlib import Path

import pytest
from _pytest.compat import legacy_path


class Factory:
    def __init__(self, base):
        self._base = Path(base)
        self._count = itertools.count()

    def getbasetemp(self):
        self._base.mkdir(parents=True, exist_ok=True)
        return self._base

    def mktemp(self, basename, numbered=True):
        name = f"{basename}{next(self._count)}" if numbered else basename
        path = self.getbasetemp() / name
        path.mkdir()
        return path


def pytest_configure(config):
    worker = getattr(config, "workerinput", None)
    run = Path(worker["stand_in_run"]) if worker else Path(config.option.basetemp)
    name = worker["workerid"] if worker else "main"
    config.stand_in_factory = Factory(run / name)
    config.stand_in_run = run


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    node.workerinput["stand_in_run"] = str(node.config.stand_in_run)


@pytest.fixture(scope="session")
def tmp_path_factory(request):
    return request.config.stand_in_factory


@pytest.fixture(scope="session")
def tmpdir_factory(tmp_path_factory):
    return tmp_path_factory


@pytest.fixture
def tmp_path(tmp_path_factory, request):
    return tmp_path_factory.mktemp(re.sub(r"\\W", "_", request.node.name)[:30])


@pytest.fixture
def tmpdir(tmp_path):
    return legacy_path(tmp_path)
"""

USES = """
pytest_plugins = ["pytester"]


def test_tmp_path(tmp_path):
    assert "stand-in" in str(tmp_path)


def test_tmpdir(tmpdir):
    assert "stand-in" in str(tmpdir)


def test_factory(tmp_path_factory):
    assert "stand-in" in str(tmp_path_factory.mktemp("x"))


def test_pytester_takes_the_replacement(pytester):
    assert "stand-in" in str(pytester.path)
"""


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(stand_in=STAND_IN, test_uses=USES)
    pytester.syspathinsert()
    return pytester


def _run(project: pytest.Pytester, *args: str) -> pytest.RunResult:
    base = project.path / "stand-in"
    return project.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "stand_in", f"--basetemp={base}", *args
    )


def test_blocking_tmpdir_replaces_all_four_fixtures(project: pytest.Pytester) -> None:
    _run(project, "-p", "no:tmpdir").assert_outcomes(passed=4)


def test_without_blocking_the_plugin_fixtures_still_win(
    project: pytest.Pytester,
) -> None:
    _run(project).assert_outcomes(passed=4)


def test_xdist_workers_join_the_run(project: pytest.Pytester) -> None:
    pytest.importorskip("xdist")
    _run(project, "-p", "no:tmpdir", "-n", "2").assert_outcomes(passed=4)
    workers = {p.name for p in (project.path / "stand-in").iterdir()}
    assert workers <= {"gw0", "gw1"}
    assert workers


def test_blocking_tmpdir_drops_its_ini_options(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\ntmp_path_retention_policy = failed\n")
    result = _run(project, "-p", "no:tmpdir", "--strict-config")
    result.stderr.fnmatch_lines(["*Unknown config option: tmp_path_retention_policy*"])
