"""Benchmarks against pytest's own tmp_path machinery (docs/goals.md, G3).

Each scenario runs once with pytest's ``TempPathFactory`` and once with
cot.tmppath. The cot.tmppath side is xfail until the core is built.

    uv run --group bench pytest benchmarks
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from _pytest.tmpdir import TempPathFactory

from cot.tmppath import Outcome, Retention, Root

not_built = pytest.mark.xfail(
    raises=NotImplementedError, reason="core not built yet (docs/goals.md)"
)

# pytest scans the base folder on every mktemp, so cost grows with siblings
ITEMS = [100, 1000]


def _pytest_factory(base: Path) -> TempPathFactory:
    return TempPathFactory(
        given_basetemp=base,
        retention_count=3,
        retention_policy="all",
        trace=lambda *args: None,
        _ispytest=True,
    )


class _Fresh:
    """Hands out a new, empty folder per benchmark round."""

    def __init__(self, base: Path) -> None:
        self._base = base
        self._count = 0

    def __call__(self) -> Path:
        self._count += 1
        return self._base / f"round{self._count}"


@pytest.fixture
def fresh(tmp_path: Path) -> _Fresh:
    return _Fresh(tmp_path)


@pytest.mark.parametrize("count", ITEMS)
def test_items_pytest(benchmark, fresh: _Fresh, count: int) -> None:
    def make() -> None:
        factory = _pytest_factory(fresh())
        for _ in range(count):
            factory.mktemp("test_something_parametrized_", numbered=True)

    benchmark.pedantic(make, rounds=5, iterations=1)


@not_built
@pytest.mark.parametrize("count", ITEMS)
def test_items_cot(benchmark, fresh: _Fresh, count: int) -> None:
    def make() -> None:
        with Root(fresh()).start_run() as run:
            for _ in range(count):
                run.item("test_something_parametrized_")

    benchmark.pedantic(make, rounds=5, iterations=1)


def _filled(path: Path) -> Path:
    path.mkdir(parents=True)
    for index in range(200):
        (path / f"f{index}").write_bytes(b"x" * 512)
    return path


def test_removal_pytest(benchmark, fresh: _Fresh) -> None:
    # what tmp_path's teardown does under tmp_path_retention_policy=failed
    def setup() -> tuple[tuple[Path], dict[str, object]]:
        return (_filled(fresh() / "item"),), {}

    benchmark.pedantic(
        lambda item: shutil.rmtree(item, ignore_errors=True),
        setup=setup,
        rounds=20,
    )


@not_built
def test_removal_cot(benchmark, fresh: _Fresh) -> None:
    root = Root(fresh(), retention=Retention(keep_failed_only=True))
    with root.start_run() as run:

        def setup() -> tuple[tuple[Path], dict[str, object]]:
            item = run.item("item")
            _filled(item / "data")
            return (item,), {}

        benchmark.pedantic(
            lambda item: run.finish_item(item, Outcome.PASSED),
            setup=setup,
            rounds=20,
        )
