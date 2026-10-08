"""``python -m cot.tmppath prune``: asks first, dry-run, or a painful flag."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from cot.tmppath import KEEP_EVERYTHING, Root
from cot.tmppath.__main__ import NO_CONFIRMATION_FLAG, main, parse_duration


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("text", "seconds"), [("30s", 30), ("15m", 900), ("12h", 43200), ("7d", 604800)]
)
def test_durations(text: str, seconds: int) -> None:
    assert parse_duration(text) == seconds


def test_without_a_terminal_it_refuses_to_guess(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["prune", str(tmp_path)], stdin=io.StringIO())
    assert exit_info.value.code == 2
    assert NO_CONFIRMATION_FLAG in capsys.readouterr().err


@pytest.mark.parametrize("prefix", ["--delete", "--delete-without-asking"])
def test_the_long_flag_cannot_be_abbreviated(tmp_path: Path, prefix: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["prune", str(tmp_path), prefix], stdin=io.StringIO())
    assert exit_info.value.code == 2


def test_needs_a_root_or_all_projects() -> None:
    with pytest.raises(SystemExit):
        main(["prune", "--dry-run"])


def test_dry_run_and_no_confirmation_exclude_each_other(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["prune", str(tmp_path), "--dry-run", NO_CONFIRMATION_FLAG])


def _old_runs(root_path: Path) -> list[Path]:
    # kept at close, so only the prune under test removes them
    root = Root(root_path, retention=KEEP_EVERYTHING)
    runs = []
    for _ in range(3):
        with root.start_run() as run:
            runs.append(run.path)
    return runs


def test_dry_run_lists_and_removes_nothing(root_path: Path) -> None:
    runs = _old_runs(root_path)
    out = io.StringIO()
    assert (
        main(["prune", str(root_path), "--dry-run", "--older-than", "0s"], stdout=out)
        == 0
    )
    assert all(path.exists() for path in runs)
    assert str(runs[-1]) in out.getvalue()


@pytest.mark.parametrize("answer", ["no", "y", "YES please", ""])
def test_anything_but_yes_aborts(root_path: Path, answer: str) -> None:
    runs = _old_runs(root_path)
    code = main(
        ["prune", str(root_path), "--older-than", "0s"],
        stdin=_Terminal(answer + "\n"),
        stdout=io.StringIO(),
    )
    assert code == 1
    assert all(path.exists() for path in runs)


def test_yes_removes_exactly_what_was_listed(root_path: Path) -> None:
    runs = _old_runs(root_path)
    out = io.StringIO()
    code = main(
        ["prune", str(root_path), "--older-than", "0s"],
        stdin=_Terminal("yes\n"),
        stdout=out,
    )
    assert code == 0
    assert not any(path.exists() for path in runs)


def test_the_long_flag_removes_without_asking(root_path: Path) -> None:
    runs = _old_runs(root_path)
    code = main(
        ["prune", str(root_path), "--older-than", "0s", NO_CONFIRMATION_FLAG],
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )
    assert code == 0
    assert not any(path.exists() for path in runs)


def _many_runs(root_path: Path, count: int) -> list[Path]:
    # oldest first; more than the default retention keeps
    root = Root(root_path, retention=KEEP_EVERYTHING)
    runs = []
    for _ in range(count):
        with root.start_run() as run:
            runs.append(run.path)
    return runs


def test_without_older_than_or_keep_it_selects_no_runs(root_path: Path) -> None:
    # the root was made to keep everything; the command cannot know that,
    # so it applies no retention of its own
    runs = _many_runs(root_path, 5)
    out = io.StringIO()
    assert main(["prune", str(root_path), "--dry-run"], stdout=out) == 0
    assert out.getvalue() == "nothing to remove\n"
    code = main(
        ["prune", str(root_path), NO_CONFIRMATION_FLAG],
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )
    assert code == 0
    assert all(path.exists() for path in runs)


def test_keep_removes_all_but_the_most_recent(root_path: Path) -> None:
    runs = _many_runs(root_path, 5)
    out = io.StringIO()
    assert main(["prune", str(root_path), "--keep", "2", "--dry-run"], stdout=out) == 0
    assert sorted(out.getvalue().splitlines()[:-1]) == sorted(map(str, runs[:3]))
    code = main(
        ["prune", str(root_path), "--keep", "2", NO_CONFIRMATION_FLAG],
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )
    assert code == 0
    assert [path.exists() for path in runs] == [False, False, False, True, True]


@pytest.mark.parametrize("keep", ["-1", "two", ""])
def test_keep_must_be_a_count(tmp_path: Path, keep: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["prune", str(tmp_path), "--keep", keep, "--dry-run"])
    assert exit_info.value.code == 2
