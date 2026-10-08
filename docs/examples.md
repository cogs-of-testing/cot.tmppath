# Examples: what cot.tmppath is for, and how it differs from pytest

*Written by Claude via Claude Code from Ronny's brief; Ronny prompted it.*

Every example on this page is run by `testing/test_examples.py`: the Python
blocks are executed and their `assert`s are the results claimed here, the
pytest files are run under plain pytest and under the cot.tmppath binding,
and the folder trees are compared with what those runs leave on disk. The
outputs are from pytest 9.1.1 and pytest-xdist 3.8 on Linux.

## The intent

cot.tmppath manages **related temporary folders**: the folders a test run,
a build or a remote worker makes, uses, and throws away again. It is a
library first, with no host built in, and pytest is one host among others.

- **One folder per run, items side by side.** A run is one invocation. All
  its folders sit directly inside the run folder, named after what the
  caller names them. Grouping goes in the name, not in extra levels.
- **Layouts are policies.** The flat layout is the default. pytest's
  `pytest-{N}/{test}{M}` layout is shipped as one example policy, not as the
  model everything else follows.
- **Hardened by default.** Folders are private (0o700) and checked to be
  owned by the user. A symlink is never followed at a level the library
  owns, and on Linux and macOS everything below a checked folder goes
  through directory descriptors. Only folders the library made are ever
  deleted; anything else is refused, never wiped.
- **Retention is decided from the whole outcome** an item had, which the
  host reports. For pytest that is setup, call and teardown together.
- **Per project, crash-safe.** The default root is
  `{temp}/cot.tmppath-{uid}/{project}`, so projects never push out each
  other's runs. A run is live while a process holding it is alive; a
  crashed run is collected as soon as its process is known to be gone, even
  when its pid was reused (the process start time is checked on Linux and
  Windows).
- **Temporary only.** Long-lived caches and workspaces are out of scope.

The goals behind each point are in [goals.md](goals.md), and how the core
keeps them in [core.md](core.md).

## The library

The examples below write to the system temp folder (`tempfile.gettempdir()`)
or to the current folder, as an application would.

### A run and its items

```python
from cot.tmppath import Root

root = Root.for_project("myproject")  # touches nothing on disk yet
with root.start_run() as run:
    first = run.item("test_login[admin]")
    again = run.item("test_login[admin]")
    data = run.item("fixtures/users.db")
    long = run.item("test_" + "x" * 100)
    scratch = run.item("scratch", process="gw0")

    # every item is a new, private folder directly in the run
    assert first.parent == again.parent == data.parent == run.path
    assert first.name == "test_login_admin"  # readable and portable
    assert again.name == "test_login_admin-1"  # unique without listing the run
    assert data.name == "fixtures_users.db"  # no hidden extra level
    assert long.name == "test_" + "x" * 42 + "-030f6238"  # same in every run
    # a process folder: its name is picked by whoever manages the processes
    assert scratch == run.path / "gw0" / "scratch"
    assert run.path.parent == root.path
```

The root is `{temp}/cot.tmppath-{uid}/myproject` (the user name instead of
the uid on Windows), and run folders are named
`run-{date}-{time}-{random}`, for example `run-20261008-112036-63b568`.
`Root(path)` takes any folder instead. What else a run holds (its start
time, which processes hold it) is in files whose names start with `.cot-`;
[core.md](core.md#on-disk) shows them.

### What a root refuses

A folder the library did not make is refused, and nothing in it is touched.

```python
from pathlib import Path

from cot.tmppath import Root, UnsafeRootError

notes = Path("notes")
notes.mkdir()
(notes / "todo.txt").write_text("do not lose me")

try:
    Root(notes).start_run()
except UnsafeRootError as error:
    assert "exists and was not created by cot.tmppath" in str(error)
    assert "nothing in it was touched" in str(error)
else:
    raise AssertionError("a foreign folder was used")
assert [p.name for p in notes.iterdir()] == ["todo.txt"]

# an empty folder holds nothing to lose: it is adopted and marked
Path("empty").mkdir()
with Root(Path("empty")).start_run() as run:
    assert run.path.parent.name == "empty"
assert (Path("empty") / ".cot-tmppath").is_file()
```

A symlink at the root, and a root other users can write to, are refused
the same way:

```python posix
import os
from pathlib import Path

from cot.tmppath import Root, UnsafeRootError

with Root(Path("build")).start_run():
    pass
Path("link").symlink_to("build", target_is_directory=True)
os.chmod("build", 0o755)

for path, problem in [
    ("link", "is a symlink or not a folder"),
    ("build", "has permissions other users can use"),
]:
    try:
        Root(Path(path)).start_run()
    except UnsafeRootError as error:
        assert problem in str(error), error
    else:
        raise AssertionError(f"{path} was used")
```

### Retention

`Retention` decides which runs and items survive. The host reports each
item's outcome with `finish_item`; with `keep_failed_only` a passed item is
moved out of the run at once (a rename) and deleted when the run closes.

```python
from pathlib import Path

from cot.tmppath import Outcome, Retention, Root

root = Root(Path("build"), retention=Retention(keep_failed_only=True, keep_runs=2))
runs = []
for _ in range(3):
    with root.start_run() as run:
        passed = run.item("test_ok")
        failed = run.item("test_broken")
        run.finish_item(passed, Outcome.PASSED)
        run.finish_item(failed, Outcome.FAILED)
        assert not passed.exists()  # gone from its place already
        runs.append(run.path)

# the newest two runs are kept, holding only what failed
assert [path.exists() for path in runs] == [False, True, True]
kept = [p.name for p in runs[-1].iterdir() if not p.name.startswith(".cot-")]
assert kept == ["test_broken"]
```

Closing a run applies retention to the whole root and returns what it
removed and what it could not remove. The library never prints:

```python
from pathlib import Path

from cot.tmppath import KEEP_EVERYTHING, Retention, Root

root = Root(Path("build"), retention=Retention(keep_runs=1))
with root.start_run() as old:
    pass
report = root.start_run().close()
assert report.removed == (old.path,)
assert report.failed == ()

# KEEP_EVERYTHING deletes nothing at all
root = Root(Path("archive"), retention=KEEP_EVERYTHING)
for _ in range(5):
    with root.start_run():
        pass
assert len(list(root.path.glob("run-*"))) == 5
```

### Several processes, one run

A manager starts the run and hands its root and id to the processes it
starts; each joins the same folder and names nothing on its own.

```python
import os
import subprocess
import sys
from pathlib import Path

from cot.tmppath import Root

WORKER = """
import os
from pathlib import Path
from cot.tmppath import Root

root = Root(Path(os.environ["RUN_ROOT"]))
with root.join_run(os.environ["RUN_ID"]) as run:
    print(run.item("download", process=os.environ["WORKER"]))
"""

with Root(Path("build")).start_run() as run:
    for worker in ("gw0", "gw1"):
        env = {**os.environ, "RUN_ROOT": str(run.path.parent), "RUN_ID": run.id}
        out = subprocess.run(
            [sys.executable, "-c", WORKER],
            env={**env, "WORKER": worker},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert Path(out.strip()) == run.path / worker / "download"
```

### Crashes

A run is live while any process holding it may be alive. A run whose
process died without closing it is collected by the next prune; a run
whose process is still running is never selected, however old.

```python
import subprocess
import sys
from pathlib import Path

from cot.tmppath import KEEP_EVERYTHING, Root

root = Root(Path("build"), retention=KEEP_EVERYTHING)
START = f"""
import os, sys, time
from pathlib import Path
from cot.tmppath import Root
run = Root(Path({str(root.path)!r})).start_run()
print(run.path, flush=True)
if sys.argv[1] == "crash":
    os._exit(1)  # dies without closing its run
time.sleep(60)
"""

crashed = subprocess.run(
    [sys.executable, "-c", START, "crash"], capture_output=True, text=True
)
crashed_run = Path(crashed.stdout.strip())
with subprocess.Popen(
    [sys.executable, "-c", START, "live"], stdout=subprocess.PIPE, text=True
) as alive:
    try:
        assert alive.stdout is not None
        alive_run = Path(alive.stdout.readline().strip())

        plan = root.plan_prune(older_than=0)  # everything unused for 0 seconds
        assert crashed_run in plan.paths
        assert alive_run not in plan.paths
        assert plan.apply().removed == (crashed_run,)
    finally:
        alive.kill()
```

### Atomic placement

`Run.place` stages a file or folder inside the run and renames it onto the
destination only when the block succeeds, so a reader never sees half of
it.

```python
from pathlib import Path

from cot.tmppath import Root

with Root(Path("build")).start_run() as run:
    with run.place(Path("package.whl")) as staging:
        staging.write_bytes(b"wheel contents")
        assert not Path("package.whl").exists()
    assert Path("package.whl").read_bytes() == b"wheel contents"

    try:
        with run.place(Path("broken.whl")) as staging:
            staging.write_bytes(b"half a wheel")
            raise ConnectionError("transfer interrupted")
    except ConnectionError:
        pass
    assert not Path("broken.whl").exists()
```

### Layouts are policies

`PytestLayout` reproduces pytest's numbering, and lists folders to find the
next number as pytest does. A host can bring its own layout: two methods
that return a candidate name, asked again when the name is taken.

```python
from pathlib import Path

from cot.tmppath import PytestLayout, Root

root = Root(Path("build"), layout=PytestLayout())
with root.start_run() as run:
    assert run.path.name == "pytest-0"
    assert run.item("test_x").name == "test_x0"
    assert run.item("test_x").name == "test_x1"
with root.start_run() as run:
    assert run.path.name == "pytest-1"


class Numbered:
    """Runs and items numbered by this object, never by listing a folder."""

    def __init__(self) -> None:
        self.count = 0

    def run_name(self, root: Path) -> str:
        self.count += 1
        return f"attempt-{self.count}"

    def item_name(self, run: Path, name: str) -> str:
        self.count += 1
        return f"{self.count:03}-{name}"


with Root(Path("custom"), layout=Numbered()).start_run() as run:
    assert run.path.name == "attempt-1"
    assert run.item("compile").name == "002-compile"
```

### Removing old runs by hand

`python -m cot.tmppath prune` (also installed as `cot-tmppath`) removes
runs that retention no longer keeps, plus, with `--older-than`, runs unused
for that long. It lists them and asks before removing anything; without a
terminal it refuses to guess:

```console
$ python -m cot.tmppath prune build --older-than 0s --dry-run
/home/alice/myproject/build/run-20261008-112122-ae1fd4
/home/alice/myproject/build/run-20261008-112122-510a40
/home/alice/myproject/build/run-20261008-112122-effd72
3 folders would be removed (dry run)
$ python -m cot.tmppath prune build --older-than 0s < /dev/null
usage: python -m cot.tmppath [-h] {prune} ...
python -m cot.tmppath: error: cannot ask for confirmation, stdin is not a terminal; use --dry-run, or --delete-without-asking-i-have-read-the-dry-run
$ python -m cot.tmppath prune build --older-than 0s --delete-without-asking-i-have-read-the-dry-run
...
$ python -m cot.tmppath prune build --dry-run
nothing to remove
```

`--all-projects` covers every project root of the current user under the
default location. Live runs are never selected, and each run is checked
again right before it is removed.

## pytest, with and without cot.tmppath

The binding is opt-in, per project. It replaces pytest's `tmp_path`,
`tmp_path_factory` and basetemp handling as a whole:

```ini
[pytest]
addopts = -p cot.tmppath.overtake_pytest
```

The comparisons below run the same files twice, as `pytest` and as
`pytest -p cot.tmppath.overtake_pytest`, from a project folder named
`myproject`, with `TMPDIR` pointing at an empty folder. The trees leave out
cot.tmppath's own `.cot-*` files.

### The folder layout

```python file=test_layout.py
import pytest


def test_plain(tmp_path):
    (tmp_path / "out.txt").write_text("ok")


@pytest.mark.parametrize("user", ["admin", "guest"])
def test_login(tmp_path, user):
    pass


def test_data(tmp_path_factory):
    tmp_path_factory.mktemp("data")
    tmp_path_factory.mktemp("data")
```

pytest numbers every folder by listing its parent, and adds `current`
symlinks:

```text tree=layout-pytest
$TMPDIR/
  pytest-of-alice/
    pytest-0/
      data0/
      data1/
      datacurrent -> data1
      test_login_admin_0/
      test_login_admin_current -> test_login_admin_0
      test_login_guest_0/
      test_login_guest_current -> test_login_guest_0
      test_plain0/
        out.txt
      test_plaincurrent -> test_plain0
    pytest-current -> pytest-0
```

cot.tmppath makes one folder per project and one per run, with the items
side by side; a name gets a suffix only when it is taken:

```text tree=layout-cot
$TMPDIR/
  cot.tmppath-1000/
    myproject/
      run-20261008-112547-81a4f4/
        data/
        data-1/
        test_login_admin/
        test_login_guest/
        test_plain/
          out.txt
```

### Which folders a failure keeps

With `tmp_path_retention_policy = failed`, both keep the folders of failed
tests only. pytest decides from the test's `call` alone, so a test whose
fixture fails in teardown loses its folder, which is the one needed to
debug it. cot.tmppath decides at the teardown report, from setup, call and
teardown together.

```ini file=pytest.ini
[pytest]
tmp_path_retention_policy = failed
```

```python file=test_outcome.py
import pytest


@pytest.fixture
def server(tmp_path):
    yield tmp_path
    raise RuntimeError("the server did not shut down")


def test_passes(tmp_path):
    pass


def test_fails(tmp_path):
    assert False


def test_teardown_error(server):
    pass
```

Both report `1 failed, 2 passed, 1 error`: `test_teardown_error` passes its
call and errors in teardown.

```text tree=outcome-pytest
$TMPDIR/
  pytest-of-alice/
    pytest-0/
      test_fails0/
      test_failscurrent -> test_fails0
    pytest-current -> pytest-0
```

```text tree=outcome-cot
$TMPDIR/
  cot.tmppath-1000/
    myproject/
      run-20261008-112548-d034fb/
        test_fails/
        test_teardown_error/
```

### `--basetemp` never deletes what cot.tmppath did not make

pytest empties the `--basetemp` folder at the start of every run, whatever
it holds. With cot.tmppath, `--basetemp` names a root that holds the runs.
It is used only if it is missing, empty, or made by cot.tmppath; anything
else is a usage error, and the folder is left alone.

```python file=test_one.py
def test_one(tmp_path):
    pass
```

```console
$ ls notes
todo.txt
$ pytest --basetemp=notes
```

```text tree=basetemp-existing-pytest
notes/
  test_one0/
  test_onecurrent -> test_one0
```

```console
$ pytest -p cot.tmppath.overtake_pytest --basetemp=notes
ERROR: --basetemp=/home/alice/myproject/notes: /home/alice/myproject/notes exists and was not created by cot.tmppath; nothing in it was touched; remove it or name another folder
```

```text tree=basetemp-existing-cot
notes/
  todo.txt
```

A new `--basetemp`, after three runs: pytest has only the last one;
cot.tmppath keeps runs as anywhere else (three by default,
`tmp_path_retention_count`).

```text tree=basetemp-new-pytest
build/
  test_one0/
  test_onecurrent -> test_one0
```

```text tree=basetemp-new-cot
build/
  run-20261008-112552-7fb0b3/
    test_one/
  run-20261008-112553-d69107/
    test_one/
  run-20261008-112553-f5e86c/
    test_one/
```

Inside the run, `tmp_path_factory.getbasetemp()` is the run folder and
`tmp_path` folders are made in it, as with pytest. `pytester` works
unchanged, including repeated inline `runpytest()` calls with the same
`--basetemp`.

### pytest-xdist

```python file=test_spread.py
import pytest


@pytest.mark.parametrize("n", range(4))
def test_item(tmp_path, n):
    pass
```

`pytest -n 2`: pytest makes one `popen-gwN` basetemp per worker inside its
run. cot.tmppath puts every worker in the controller's run, in a folder the
controller names. In both, a worker's `getbasetemp()` is its own folder and
`getbasetemp().parent` is the run. Which worker runs which test varies.

```text
$TMPDIR/
  pytest-of-alice/
    pytest-0/
      popen-gw0/
        test_item_0_0/
        test_item_0_current -> test_item_0_0
        test_item_1_0/
        test_item_1_current -> test_item_1_0
      popen-gw1/
        test_item_2_0/
        test_item_2_current -> test_item_2_0
        test_item_3_0/
        test_item_3_current -> test_item_3_0
    pytest-current -> pytest-0
```

```text
$TMPDIR/
  cot.tmppath-1000/
    myproject/
      run-20261008-112056-08260f/
        gw0/
          test_item_0/
          test_item_1/
        gw1/
          test_item_2/
          test_item_3/
```

### Projects keep their own runs

pytest keeps the last three runs of *all* projects of a user in one
`pytest-of-{user}` folder. Run `alpha` once and `beta` three times, and
alpha's run is gone. cot.tmppath counts runs per project. Both projects
hold `test_one.py` from above.

```text tree=projects-pytest
$TMPDIR/
  pytest-of-alice/
    pytest-1/
      test_one0/
      test_onecurrent -> test_one0
    pytest-2/
      test_one0/
      test_onecurrent -> test_one0
    pytest-3/
      test_one0/
      test_onecurrent -> test_one0
    pytest-current -> pytest-3
```

```text tree=projects-cot
$TMPDIR/
  cot.tmppath-1000/
    alpha/
      run-20261008-112549-5d420d/
        test_one/
    beta/
      run-20261008-112550-09e399/
        test_one/
      run-20261008-112550-8d0293/
        test_one/
      run-20261008-112550-a07770/
        test_one/
```

### `mktemp(numbered=False)` and `tmpdir`

```python file=test_names.py
def test_names(tmp_path_factory):
    first = tmp_path_factory.mktemp("data", numbered=False)
    print("first:", first.name)
    try:
        second = tmp_path_factory.mktemp("data", numbered=False)
    except FileExistsError:
        print("second: FileExistsError")
    else:
        print("second:", second.name)


def test_legacy(tmpdir):
    pass
```

pytest gives `numbered=False` the exact name, and raises the second time.
cot.tmppath always makes a new folder, so the name is not guaranteed:

```console
$ pytest -s
first: data
second: FileExistsError
$ pytest -s -p cot.tmppath.overtake_pytest
first: data
second: data-1
...
E       fixture 'tmpdir' not found
```

The `py.path` fixtures `tmpdir` and `tmpdir_factory` are deliberately not
provided: a suite that still uses them gets "fixture not found" and moves to
`tmp_path` and `tmp_path_factory`.

### What stays the same

- `tmp_path` and `tmp_path_factory` with `mktemp(basename)` and
  `getbasetemp()`; `pytester` works unchanged.
- `config._tmp_path_factory` is set, to the cot.tmppath factory, so plugins
  that use it (pytest-xdist among them) get cot.tmppath's folders.
- `tmp_path_retention_count` and `tmp_path_retention_policy`
  (`all`, `failed`, `none`) keep their meaning and stay valid under
  `--strict-config`.
- A session that asks for no temporary folder creates nothing.

### Speed

pytest lists the base folder on every `mktemp` to find the next number;
cot.tmppath makes an item with one `mkdir`, and moves a removed item out of
the way with one rename. The measured numbers are in
[core.md](core.md#measured), from `uv run --group bench pytest benchmarks`.

| What | pytest | cot.tmppath |
|---|---|---|
| layout | `pytest-of-{user}/pytest-{N}/{test}{M}` | `cot.tmppath-{uid}/{project}/{run}/{item}` |
| xdist | `pytest-{N}/popen-gwN/{test}{M}` | `{run}/gwN/{item}` |
| runs kept | 3 for all projects together | 3 per project |
| `failed` policy | judged on `call` | judged on setup, call and teardown |
| `--basetemp` | emptied, whatever it holds | a root; refused unless cot.tmppath made it or it is empty |
| retention with `--basetemp` | none, only the last run | as anywhere else |
| crashed runs | removed after three days | removed once the process is gone |
| `mktemp(numbered=False)` | exact name, raises if taken | a new folder, name not guaranteed |
| `tmpdir`, `tmpdir_factory` | provided | not provided |
| making a folder | lists the parent | one `mkdir` |
