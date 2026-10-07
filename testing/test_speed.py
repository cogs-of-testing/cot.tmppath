"""G3: cheaper than pytest's tmp_path where it matters.

These pin the mechanisms behind the speed goal. Timing comparisons against
pytest's tmp_path belong in benchmarks, not here.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cot.tmppath import Outcome, Retention, Root


def _no_listing(*args: object, **kwargs: object) -> object:
    raise AssertionError("directory listed on the hot path")


def test_making_an_item_does_not_list_the_run_folder(
    root_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Root(root_path).start_run() as run:
        run.item("warm")
        # closing applies retention, which lists the root; that is not the
        # hot path
        with monkeypatch.context() as patched:
            patched.setattr(os, "scandir", _no_listing)
            patched.setattr(os, "listdir", _no_listing)
            for _ in range(100):
                run.item("test_x")


def test_default_root_touches_nothing_until_a_run_starts(tmp_path: Path) -> None:
    Root.for_project("demo", temproot=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_removed_item_is_gone_from_its_place_at_once(root_path: Path) -> None:
    root = Root(root_path, retention=Retention(keep_failed_only=True))
    with root.start_run() as run:
        item = run.item("test_passes")
        (item / "data").write_bytes(b"x" * 1024)
        run.finish_item(item, Outcome.PASSED)
        assert not item.exists()
