"""G6: a core with no host."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest

import cot.tmppath
from conftest import not_built, run_python
from cot.tmppath import Outcome, Retention, Root


def test_importing_does_not_import_pytest() -> None:
    loaded = run_python(
        """
        import sys
        import cot.tmppath
        print(sorted(m for m in sys.modules if m.split(".")[0] in {"pytest", "_pytest"}))
        """
    )
    assert loaded.strip() == "[]"


def test_has_no_runtime_dependencies() -> None:
    assert not importlib.metadata.requires("cot.tmppath")


def test_is_pure_python() -> None:
    package = Path(cot.tmppath.__file__).parent
    suffixes = {p.suffix for p in package.rglob("*") if p.is_file()}
    assert suffixes <= {".py", ".pyc", ".typed", ""}


@not_built
def test_never_prints(root_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    root = Root(root_path, retention=Retention(keep_failed_only=True, keep_runs=0))
    with root.start_run() as run:
        run.finish_item(run.item("test_x"), Outcome.PASSED)
    root.prune()
    assert capfd.readouterr() == ("", "")
