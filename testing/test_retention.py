"""G4: retention that is correct and explicit."""

from __future__ import annotations

from pathlib import Path

from conftest import not_built
from cot.tmppath import Outcome, Retention, Root

pytestmark = not_built


def test_keep_failed_only_keeps_failed_items(root_path: Path) -> None:
    root = Root(root_path, retention=Retention(keep_failed_only=True))
    with root.start_run() as run:
        passed, failed = run.item("test_ok"), run.item("test_bad")
        run.finish_item(passed, Outcome.PASSED)
        run.finish_item(failed, Outcome.FAILED)
    assert not passed.exists()
    assert failed.is_dir()


def test_custom_root_keeps_the_last_runs(root_path: Path) -> None:
    root = Root(root_path, retention=Retention(keep_runs=3))
    runs = []
    for _ in range(5):
        with root.start_run() as run:
            runs.append(run.path)
    assert [path.exists() for path in runs] == [False, False, True, True, True]


def test_projects_do_not_push_out_each_others_runs(tmp_path: Path) -> None:
    with Root.for_project("a", temproot=tmp_path).start_run() as run_a:
        kept = run_a.path
    project_b = Root.for_project("b", temproot=tmp_path)
    for _ in range(5):
        with project_b.start_run():
            pass
    assert kept.is_dir()


def test_closing_reports_what_was_removed(root_path: Path) -> None:
    root = Root(root_path, retention=Retention(keep_runs=1))
    with root.start_run() as old:
        pass
    run = root.start_run()
    report = run.close()
    assert old.path in report.removed
    assert report.failed == ()
