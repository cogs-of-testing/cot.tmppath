"""G4: retention that is correct and explicit."""

from __future__ import annotations

from pathlib import Path

from cot.tmppath import KEEP_EVERYTHING, Outcome, Retention, Root


def test_keep_failed_only_keeps_failed_items(root_path: Path) -> None:
    root = Root(root_path, retention=Retention(keep_failed_only=True))
    with root.start_run() as run:
        passed, failed = run.item("test_ok"), run.item("test_bad")
        run.finish_item(passed, Outcome.PASSED)
        run.finish_item(failed, Outcome.FAILED)
    assert not passed.exists()
    assert failed.is_dir()


def test_items_in_a_process_folder_follow_retention(root_path: Path) -> None:
    root = Root(root_path, retention=Retention(keep_failed_only=True))
    with root.start_run() as run:
        passed = run.item("test_ok", process="gw0")
        failed = run.item("test_bad", process="gw0")
        assert passed.parent == failed.parent == run.process_folder("gw0")
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


def test_project_names_sharing_a_prefix_keep_their_own_runs(tmp_path: Path) -> None:
    # runs of both live side by side in the user folder, told apart by marker
    project_a = Root.for_project(
        "a", temproot=tmp_path, retention=Retention(keep_runs=1)
    )
    with project_a.start_run() as run_a:
        kept = run_a.path
    project_ab = Root.for_project(
        "a-b", temproot=tmp_path, retention=Retention(keep_runs=1)
    )
    for _ in range(3):
        with project_ab.start_run():
            pass
    assert kept.is_dir()
    assert len(list(project_ab.path.glob("a-b-*"))) == 1


def test_all_projects_are_found_from_their_runs(tmp_path: Path) -> None:
    for project in ("beta", "alpha"):
        with Root.for_project(project, temproot=tmp_path).start_run():
            pass
    found = Root.all_projects(temproot=tmp_path)
    assert [root.project for root in found] == ["alpha", "beta"]


def test_all_projects_take_a_retention(tmp_path: Path) -> None:
    for project in ("alpha", "beta"):
        Root.for_project(project, temproot=tmp_path).start_run().close()
    found = Root.all_projects(temproot=tmp_path, retention=KEEP_EVERYTHING)
    assert [root.retention for root in found] == [KEEP_EVERYTHING] * 2
