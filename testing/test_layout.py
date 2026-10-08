"""G2: one folder per run, flat inside; layouts are policies."""

from __future__ import annotations

import re
from pathlib import Path

from cot.tmppath import PytestLayout, Root


def test_items_sit_directly_in_the_run_folder(root_path: Path) -> None:
    with Root(root_path).start_run() as run:
        assert run.path.parent == root_path
        assert run.item("test_one").parent == run.path
        assert run.item("module/fixture").parent == run.path


def test_item_names_are_readable(root_path: Path) -> None:
    with Root(root_path).start_run() as run:
        assert run.item("test_foo[a-b]").name.startswith("test_foo")


def test_same_name_gets_distinct_folders(root_path: Path) -> None:
    with Root(root_path).start_run() as run:
        first, second = run.item("test_x"), run.item("test_x")
        assert first != second
        assert first.is_dir()
        assert second.is_dir()


def test_long_names_are_shortened_deterministically(root_path: Path) -> None:
    long_a = "test_" + "a" * 300 + "[param-1]"
    long_b = "test_" + "a" * 300 + "[param-2]"
    root = Root(root_path)
    with root.start_run() as first_run:
        first = first_run.item(long_a)
        other = first_run.item(long_b)
    with root.start_run() as second_run:
        again = second_run.item(long_a)
    assert len(first.name) <= 64
    assert first.name != other.name
    assert first.name == again.name


def test_pytest_layout_is_an_example_policy(root_path: Path) -> None:
    with Root(root_path, layout=PytestLayout()).start_run() as run:
        assert re.fullmatch(r"pytest-\d+", run.path.name)
        assert re.fullmatch(r"test_x\d+", run.item("test_x").name)


def test_process_folders_use_the_managers_name(root_path: Path) -> None:
    with Root(root_path).start_run() as run:
        first = run.process_folder("gw0")
        assert first == run.path / "gw0"
        assert run.process_folder("gw0") == first


def test_default_root_has_a_private_per_user_folder(tmp_path: Path) -> None:
    with Root.for_project("demo", temproot=tmp_path).start_run() as run:
        (user_folder,) = tmp_path.iterdir()
        assert user_folder.name.startswith("cot-")
        # the project names the run, so an item is two levels down
        assert run.path.parent == user_folder
        assert re.fullmatch(r"demo-\d{8}-\d{6}-[0-9a-f]{6}", run.path.name)
