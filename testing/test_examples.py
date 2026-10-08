"""docs/examples.md, executed: every example there is run here.

- A ``python`` block is a library example. Each one runs on its own, in a
  fresh folder that is also the system temp folder, and its asserts are the
  results the page claims.
- A ``python file=NAME`` or ``ini file=NAME`` block is a file of a pytest
  comparison. The scenarios below write those files, run plain pytest and
  pytest with ``-p cot.tmppath.overtake_pytest``, and compare the folders
  left behind with the page's ``text tree=NAME`` blocks.
- The shell sessions are checked by the tests that name them.
"""

from __future__ import annotations

import io
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from cot.tmppath import KEEP_EVERYTHING, Root
from cot.tmppath.__main__ import NO_CONFIRMATION_FLAG, main

pytest_plugins = ["pytester"]

PAGE = Path(__file__).parent.parent / "docs" / "examples.md"
PLUGIN = "cot.tmppath.overtake_pytest"

_FENCE = re.compile(r"^```(\w+)([^\n]*)\n(.*?)^```$", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class Block:
    language: str
    attributes: dict[str, str]
    flags: frozenset[str]
    text: str
    line: int
    heading: str


def _blocks() -> list[Block]:
    page = PAGE.read_text(encoding="utf-8")
    found = []
    headings = [
        (m.start(), m[1]) for m in re.finditer(r"^#{2,} (.+)$", page, re.MULTILINE)
    ]
    for match in _FENCE.finditer(page):
        heading = [text for start, text in headings if start < match.start()][-1]
        words = match[2].split()
        found.append(
            Block(
                language=match[1],
                attributes=dict(w.split("=", 1) for w in words if "=" in w),
                flags=frozenset(w for w in words if "=" not in w),
                text=match[3],
                line=page.count("\n", 0, match.start()) + 1,
                heading=re.sub(r"\W+", "-", heading.lower()).strip("-"),
            )
        )
    return found


def _named(key: str) -> dict[str, str]:
    return {b.attributes[key]: b.text for b in _blocks() if key in b.attributes}


_LIBRARY = [
    b for b in _blocks() if b.language == "python" and "file" not in b.attributes
]


@pytest.mark.parametrize("block", _LIBRARY, ids=[b.heading for b in _LIBRARY])
def test_library_example(
    block: Block, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if "posix" in block.flags and sys.platform == "win32":
        pytest.skip("POSIX example")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    code = compile(block.text, f"{PAGE}:{block.line}", "exec")
    exec(code, {"__name__": "__example__"})  # noqa: S102 - the page's own code


# -- pytest comparisons -------------------------------------------------------

_NORMALIZE = [
    (re.compile(r"(\w+)-\d{8}-\d{6}-[0-9a-f]{6}"), r"\1-<id>"),
    (re.compile(r"pytest-of-[^/\s]+"), "pytest-of-<user>"),
    (re.compile(r"\bcot-[^/\s]+"), "cot-<uid>"),
]


def _normalize(text: str) -> str:
    for pattern, replacement in _NORMALIZE:
        text = pattern.sub(replacement, text)
    return text.strip()


def render(path: Path, label: str) -> str:
    """``path`` as the page draws it; cot.tmppath's ``.cot-*`` files left out."""
    lines = [f"{label}/"]

    def walk(folder: Path, depth: int) -> None:
        for child in sorted(folder.iterdir(), key=lambda p: p.name):
            if child.name.startswith(".cot-"):
                continue
            indent = "  " * depth
            if child.is_symlink():
                target = child.readlink().name
                lines.append(f"{indent}{child.name} -> {target}")
            elif child.is_dir():
                lines.append(f"{indent}{child.name}/")
                walk(child, depth + 1)
            else:
                lines.append(f"{indent}{child.name}")

    walk(path, 1)
    return "\n".join(lines)


def _pytest(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.run(sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args)


@pytest.fixture
def project(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty project folder named ``myproject``, as the cwd."""
    folder = pytester.mkdir("myproject")
    monkeypatch.chdir(folder)
    return folder


def _write(folder: Path, *names: str) -> None:
    files = _named("file")
    for name in names:
        (folder / name).write_text(files[name], encoding="utf-8")


def _temproot(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, name: str
) -> Path:
    temproot = pytester.mkdir(name)
    for var in ("TMPDIR", "TEMP", "TMP", "PYTEST_DEBUG_TEMPROOT"):
        monkeypatch.setenv(var, str(temproot))
    return temproot


def _check_tree(name: str, actual: str) -> None:
    assert _normalize(actual) == _normalize(_named("tree")[name])


# pytest's "current" symlinks are not made on Windows without privileges
needs_symlinks = pytest.mark.skipif(sys.platform == "win32", reason="symlinks")


@needs_symlinks
@pytest.mark.parametrize(
    ("files", "trees"),
    [
        (["test_layout.py"], "layout"),
        (["test_outcome.py", "pytest.ini"], "outcome"),
    ],
    ids=["layout", "outcome"],
)
def test_comparison(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    files: list[str],
    trees: str,
) -> None:
    _write(project, *files)
    for plugin, side in [([], "pytest"), (["-p", PLUGIN], "cot")]:
        temproot = _temproot(pytester, monkeypatch, f"tmp-{side}")
        _pytest(pytester, *plugin)
        _check_tree(f"{trees}-{side}", render(temproot, "$TMPDIR"))


@needs_symlinks
def test_projects_keep_their_own_runs(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("alpha", "beta"):
        _write(pytester.mkdir(name), "test_one.py")
    for plugin, side in [([], "pytest"), (["-p", PLUGIN], "cot")]:
        temproot = _temproot(pytester, monkeypatch, f"tmp-{side}")
        for name in ["alpha", "beta", "beta", "beta"]:
            monkeypatch.chdir(pytester.path / name)
            _pytest(pytester, *plugin).assert_outcomes(passed=1)
        _check_tree(f"projects-{side}", render(temproot, "$TMPDIR"))


@needs_symlinks
def test_basetemp(pytester: pytest.Pytester, project: Path) -> None:
    _write(project, "test_one.py")
    for side, plugin in [("pytest", []), ("cot", ["-p", PLUGIN])]:
        # an existing folder: pytest empties it, cot.tmppath refuses it
        notes = pytester.mkdir(f"notes-{side}")
        (notes / "todo.txt").write_text("do not lose me")
        result = _pytest(pytester, *plugin, f"--basetemp={notes}")
        _check_tree(f"basetemp-existing-{side}", render(notes, "notes"))
        if side == "cot":
            assert result.ret == pytest.ExitCode.USAGE_ERROR
            result.stderr.fnmatch_lines(
                [
                    (
                        "ERROR: --basetemp=*notes-cot*not created by cot.tmppath;"
                        " nothing in it was touched*"
                    )
                ]
            )
        # a new folder, three runs: pytest keeps the last, cot.tmppath all three
        fresh = project / f"build-{side}"
        for _ in range(3):
            _pytest(pytester, *plugin, f"--basetemp={fresh}").assert_outcomes(passed=1)
        _check_tree(f"basetemp-new-{side}", render(fresh, "build"))


def test_xdist_layout(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    pytest.importorskip("xdist")
    _write(project, "test_spread.py")
    names = {f"test_item_{n}" for n in range(4)}

    temproot = _temproot(pytester, monkeypatch, "tmp-pytest")
    _pytest(pytester, "-n", "2").assert_outcomes(passed=4)
    (user,) = temproot.iterdir()
    run = user / "pytest-0"
    assert sorted(p.name for p in run.iterdir()) == ["popen-gw0", "popen-gw1"]
    made = {p.name for w in run.iterdir() for p in w.iterdir() if p.name[-1] == "0"}
    assert made == {f"{name}_0" for name in names}

    temproot = _temproot(pytester, monkeypatch, "tmp-cot")
    _pytest(pytester, "-p", PLUGIN, "-n", "2").assert_outcomes(passed=4)
    (run,) = (temproot / user_folder(temproot)).glob("myproject-*")
    workers = [p for p in run.iterdir() if not p.name.startswith(".cot-")]
    assert sorted(p.name for p in workers) == ["gw0", "gw1"]
    assert {p.name for w in workers for p in w.iterdir()} == names


def user_folder(temproot: Path) -> str:
    (folder,) = temproot.iterdir()
    assert folder.name.startswith("cot-")
    return folder.name


def test_mktemp_and_tmpdir(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    _write(project, "test_names.py")
    _temproot(pytester, monkeypatch, "temproot")
    plain = _pytest(pytester, "-s")
    plain.stdout.fnmatch_lines(["*first: data", "second: FileExistsError"])
    plain.assert_outcomes(passed=2)
    cot = _pytest(pytester, "-s", "-p", PLUGIN)
    cot.stdout.fnmatch_lines(
        ["*first: data", "second: data-1", "*fixture 'tmpdir' not found*"]
    )
    cot.assert_outcomes(passed=1, errors=1)


# -- the prune command ----------------------------------------------------------


def test_prune_session(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = Root(tmp_path / "build", retention=KEEP_EVERYTHING)
    runs = []
    for _ in range(3):
        with root.start_run() as run:
            runs.append(run.path)
    args = ["prune", str(root.path), "--older-than", "0s"]

    assert main([*args, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == "3 folders would be removed (dry run)"
    assert all(path.exists() for path in runs)

    with pytest.raises(SystemExit) as exit_info:
        main(args, stdin=io.StringIO())
    assert exit_info.value.code == 2
    assert "cannot ask for confirmation, stdin is not a terminal" in (
        capsys.readouterr().err
    )

    assert main([*args, NO_CONFIRMATION_FLAG]) == 0
    assert not any(path.exists() for path in runs)
    assert main(["prune", str(root.path), "--dry-run"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "nothing to remove"
