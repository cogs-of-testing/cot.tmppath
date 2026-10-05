# Research: temporary paths in pytest

Initial research for `cot.tmppath`: how pytest provides temporary
directories today, what has gone wrong with it over time, and which problems
are still open. The aim is to know the ground before designing anything.

Sources, as of 2026-10-05:

- pytest 9.1.1 source: `src/_pytest/tmpdir.py`, `src/_pytest/pathlib.py`,
  `src/_pytest/legacypath.py`, `src/_pytest/main.py`
- pytest docs: `doc/en/how-to/tmp_path.rst`, `doc/en/changelog.rst`
- pytest-xdist source: `xdist/workermanage.py`, `xdist/remote.py`, and its how-to docs
- the pytest issues and PRs linked inline

Statements marked **(verified)** were reproduced locally against pytest 9.1.1.
Everything else is read from the source or the linked issue.

## 1. How it works today

### Public surface

| Name | Scope | Returns |
|---|---|---|
| `tmp_path` | function | `pathlib.Path`, unique per test item |
| `tmp_path_factory` | session | `pytest.TempPathFactory` (`mktemp(basename, numbered=True)`, `getbasetemp()`) |
| `tmpdir` / `tmpdir_factory` | function / session | `py.path.local` wrappers, provided by the `legacypath` plugin; discouraged ([#9937](https://github.com/pytest-dev/pytest/issues/9937)), disable with `-p no:legacypath` |
| `--basetemp=DIR` | option | use `DIR` as base, cleared before the run |
| `tmp_path_retention_count` | ini, default `"3"` (a string, see [#13904](https://github.com/pytest-dev/pytest/issues/13904)) | how many `pytest-N` session dirs to keep |
| `tmp_path_retention_policy` | ini, default `all` | `all` / `failed` / `none` ([#8141](https://github.com/pytest-dev/pytest/issues/8141), 7.3.0) |
| `PYTEST_DEBUG_TEMPROOT` | env | override `tempfile.gettempdir()` as the root |

### Layout

Without `--basetemp`:

```
{temproot}/pytest-of-{user}/pytest-{N}/{testname}{M}/
{temproot}/pytest-of-{user}/pytest-current -> pytest-{N}
{temproot}/pytest-of-{user}/pytest-{N}.lock
```

With `--basetemp=DIR`: `DIR/{testname}{M}/`, no numbering at the session
level, no retention.

### Lifecycle

1. `pytest_configure` creates a `TempPathFactory` and stores it on the
   **private** `config._tmp_path_factory`; the session fixture just returns it.
   Nothing is created on disk yet.
2. The first `getbasetemp()` call creates the base dir lazily:
   - given `--basetemp`: `rm_rf` it if it exists, `mkdir(0o700)`, `resolve()`;
   - otherwise: `resolve()` the temproot, create `pytest-of-{user}` (0o700),
     check it is not a symlink and is owned by the current uid, strip group
     and other permission bits, then `make_numbered_dir_with_cleanup`, which
     creates `pytest-{N}`, writes a `.lock` file containing the pid (only when
     `keep != 0`), and registers cleanup of older numbered dirs on an
     `ExitStack` that is closed at `pytest_sessionfinish` and, as a fallback,
     `atexit`.
3. `tmp_path` takes `request.node.name`, replaces every `\W` with `_`,
   truncates to 30 characters, and calls `mktemp(name, numbered=True)`, which
   scans the base dir for the highest existing suffix, creates `name{max+1}`
   with mode 0o700 (retrying up to 10 times), and refreshes a
   `{name}current` symlink.
4. A `pytest_runtest_makereport` hook wrapper records `rep.passed` per phase
   in the item's stash. On `tmp_path` teardown, policy `failed` removes the
   directory if the `call` phase passed, with `rmtree(ignore_errors=True)`.
5. `pytest_sessionfinish`: if the exit status is 0, policy is `failed` and
   `--basetemp` was not given, remove the whole base dir. Then remove dead
   symlinks and close the `ExitStack`, which deletes numbered dirs older than
   `N - keep` whose lock is missing or older than 3 days (`LOCK_TIMEOUT`), by
   renaming each to `garbage-{uuid}` and `rm_rf`-ing it.

### pytest-xdist

The controller calls `config._tmp_path_factory.getbasetemp()` and passes
`--basetemp={base}/popen-{gwN}` to each **popen** worker
(`xdist/workermanage.py`). Consequences:

- xdist depends on the private `config._tmp_path_factory` attribute. A pytest
  refactor that removed this indirection broke xdist and had to be reverted
  ([#6767](https://github.com/pytest-dev/pytest/pull/6767), reverted in
  [#6992](https://github.com/pytest-dev/pytest/issues/6992)).
- Workers see a *given* basetemp, so they use the `--basetemp` code path:
  no retention of their own, and the "remove everything on success" step in
  `pytest_sessionfinish` never runs in a worker.
- Remote (non-popen) workers get no basetemp at all and fall back to their
  own `pytest-of-{user}`.
- Session-scoped fixtures run once *per worker*. The documented workaround is
  `tmp_path_factory.getbasetemp().parent` as a shared directory plus a
  `FileLock` ([xdist how-to](https://pytest-xdist.readthedocs.io/en/latest/how-to.html)).
  This is a convention that depends on the exact layout above.

## 2. History of problems (fixed)

Grouped by theme, from `doc/en/changelog.rst`. They show where the hard
parts are.

**Security and permissions**

- [#8414](https://github.com/pytest-dev/pytest/issues/8414) (6.2.3): dirs
  under `/tmp` were world-readable, and a pre-existing
  `/tmp/pytest-of-<user>` owned by *another* user was silently reused, which
  let that user take over the victim's temp dirs. Fixed with 0o700 and an
  ownership check.
- [#14343](https://github.com/pytest-dev/pytest/issues/14343) (9.0.3,
  CVE-2025-71176 / GHSA-6w46-j5rx-g56g): the ownership check followed
  symlinks, so a planted symlink at the predictable path could redirect the
  temp root. Fixed by `stat`/`chmod` with `follow_symlinks=False` and
  rejecting a symlinked root.
- Remaining by design (comment in `tmpdir.py`): anyone can `mkdir
  /tmp/pytest-of-otheruser` and make `otheruser`'s runs fail. Accepted as a
  DoS nuisance; the workaround is `TMPDIR` or `--basetemp`.
- [#7119](https://github.com/pytest-dev/pytest/issues/7119) (6.0): `--basetemp`
  is deleted blindly, so pytest now refuses an empty value, the cwd, or any
  ancestor of it (`validate_basetemp` in `main.py`). Any *other* directory,
  such as `$HOME/project-data`, is still wiped without asking.

**Concurrency and cleanup races**

- [#1120](https://github.com/pytest-dev/pytest/issues/1120) (6.0): dirs not
  removed properly with parallel pytest instances.
- [#5456](https://github.com/pytest-dev/pytest/issues/5456) (6.0): race when
  removing lock files.
- [#6044](https://github.com/pytest-dev/pytest/issues/6044) (5.2.2): two
  processes deleting the same old dir raised `FileNotFoundError`; now ignored.
- [#7491](https://github.com/pytest-dev/pytest/issues/7491) (6.0): an
  inaccessible lock file raised; now the dir is treated as not deletable.
- [#7911](https://github.com/pytest-dev/pytest/issues/7911) (6.1.2 / 6.2.0):
  locks were considered stale after 3 hours, so long suites had their live
  dirs deleted by a concurrent run. Raised to 3 days. This is a heuristic: a
  crashed run's dirs survive for 3 days, a run longer than 3 days is still at
  risk.

**Deletion robustness**

- [#4262](https://github.com/pytest-dev/pytest/issues/4262),
  [#5524](https://github.com/pytest-dev/pytest/issues/5524): read-only files
  blocked deletion, so a second run with the same `--basetemp` crashed.
  `rm_rf` now chmods and retries on `PermissionError`.
- [#6755](https://github.com/pytest-dev/pytest/issues/6755) (5.4.3): paths
  over 260 characters could not be deleted on Windows; deletion now uses the
  `\\?\` extended-length prefix. Creation does not.
- [#10890](https://github.com/pytest-dev/pytest/issues/10890) (7.3.1):
  `shutil.rmtree(onerror=)` deprecation on Python 3.12.

**Paths and identity**

- [#4425](https://github.com/pytest-dev/pytest/issues/4425),
  [#4427](https://github.com/pytest-dev/pytest/issues/4427): relative
  `--basetemp` must be made absolute; `abspath` is used instead of `resolve()`
  because `resolve()` behaved differently across platforms.
- [#4653](https://github.com/pytest-dev/pytest/issues/4653),
  [#4681](https://github.com/pytest-dev/pytest/issues/4681): `tmp_path` is
  always the resolved real path. On macOS that is `/private/var/...`, not
  what `tempfile.gettempdir()` returns, which surprises tests comparing paths.
- [#4680](https://github.com/pytest-dev/pytest/issues/4680): `tmpdir` and
  `tmp_path` must be the same directory.
- [#8317](https://github.com/pytest-dev/pytest/issues/8317),
  [#11875](https://github.com/pytest-dev/pytest/issues/11875),
  [#10765](https://github.com/pytest-dev/pytest/issues/10765): `getuser()`
  returning illegal characters, raising differently on Python 3.13, and
  `os.getuid` missing on emscripten. The user name is load-bearing for the
  path and fails in exotic environments.
- [#11895](https://github.com/pytest-dev/pytest/issues/11895) /
  [#12039](https://github.com/pytest-dev/pytest/issues/12039) (8.0.2 / 8.1.1):
  a fix for Windows short paths made collection follow the `*current`
  symlinks pytest itself creates, so tests under a temp dir were collected
  4 to 8 times on Windows CI. Fixed by not following symlinks during
  collection.
- issue 354 of the pre-GitHub tracker (3.1.1): long
  parametrized names created over-long file names, hence the 30-character
  truncation.
- [#5686](https://github.com/pytest-dev/pytest/issues/5686) (5.4):
  `mktemp` rejects absolute and non-normalised names.

**Performance**

- [#10896](https://github.com/pytest-dev/pytest/issues/10896) (7.3.1): the
  retention policy introduced a performance regression.
- `make_numbered_dir` lists the whole parent directory on every `mktemp` to
  find the next number, so creating *n* dirs under one basetemp costs
  O(n²) directory entries read. This is why `pytest-of-{user}` exists
  (3.1.1 changelog: "use a sub-directory ... to speed up").

## 3. Open problems and caveats

**Retention**

- **No retention with `--basetemp`**
  ([#10829](https://github.com/pytest-dev/pytest/issues/10829), open). The
  option conflates *where* the root is with *whether to number and keep*.
  Proposals: a separate temproot option, or a `--make-basetemp` mode. Only
  documented so far ([#12912](https://github.com/pytest-dev/pytest/pull/12912)).
- **Retention is per user, not per project.** All projects share
  `pytest-of-{user}` and one counter. Running project B three times deletes
  project A's last run, and A's `retention_count` is overridden by whichever
  project runs next. (From `cleanup_candidates`, which keeps the
  highest *N* numbers in the shared root.)
- **`failed` only looks at the `call` phase (verified).** The fixture removes
  the dir when `result.get("call", True)` is true. A test that errors in
  setup has no `call` entry and counts as passed; a teardown failure happens
  after `tmp_path`'s own teardown has already run. With
  `tmp_path_retention_policy=failed`, a test whose fixture wrote to
  `tmp_path` and then raised in setup or teardown loses its directory, which
  is exactly the case you want to debug.
- **Deletion failures are silent.** Policy `failed` uses
  `rmtree(ignore_errors=True)`, and cleanup of old dirs swallows `OSError`, so
  a leaked open handle (common on Windows) or a permission problem quietly
  leaves data behind; `garbage-*` dirs accumulate until a later run manages.
- **Crash leaves a 3-day lock.** A killed run keeps its lock until
  `LOCK_TIMEOUT` (3 days) passes, and its dir is not counted out by number
  until then.

**Concurrency**

- **Concurrent runs and `--basetemp`**
  ([#11790](https://github.com/pytest-dev/pytest/issues/11790), open, docs).
  The how-to says the dir is unique per test invocation. Without
  `--basetemp`, separate processes (tox environments, two terminals) do get
  distinct `pytest-{N}` dirs through the numbering retry loop, and
  `pytest-current` points at whichever run started last. With a shared
  `--basetemp`, the second run `rm_rf`s the first run's live directory. The
  advice is to give each concurrent run its own `--basetemp`, which turns
  retention off (#10829).
- **xdist coordination relies on layout.** Sharing data across workers needs
  `getbasetemp().parent`, which is only right for popen workers and only
  while xdist keeps its `popen-gwN` naming. There is no API for "the run's
  shared temp dir".
- **Private attribute contract.** xdist (and other plugins) reach into
  `config._tmp_path_factory`; the factory's constructor is private
  (`_ispytest`) and its retention fields are read directly by the fixture.

**Naming**

- 30-character truncation plus a numeric suffix makes parametrized tests hard
  to find on disk: `test_something_with_a_long_na0`, `...na1`, and so on, in
  run order. `\W` is Unicode-aware, so non-ASCII test names produce non-ASCII
  directory names.
- A `{name}current` symlink is refreshed on every `mktemp`. On Windows,
  symlinks need privileges or developer mode, and the symlinks are what
  caused [#12039](https://github.com/pytest-dev/pytest/issues/12039).
- Windows `MAX_PATH`: deletion handles long paths, creation inside tests
  does not. Deep temp roots (`C:\Users\...\AppData\Local\Temp\pytest-of-x\pytest-123\`)
  leave little room under 260 characters.

**Scope and API shape**

- Only function scope (`tmp_path`) and "make your own" (`tmp_path_factory`)
  exist. There is no class- or module-scoped temp dir; users build them from
  the factory and then lose naming and retention-by-outcome.
- `tmp_path` does not change the working directory; users combine it with
  `monkeypatch.chdir`. Nothing isolates `HOME` or `TMPDIR` for code under
  test, except inside `pytester`
  ([#4956](https://github.com/pytest-dev/pytest/issues/4956)), so code that
  calls `tempfile.mkdtemp()` writes outside the managed tree and is never
  retained or cleaned.
- Retention is all-or-nothing per policy. There is no per-test opt-in to keep
  a dir, no size limit, and no age limit other than the lock timeout.
- The library is tied to pytest: `TempPathFactory` needs a pytest `Config`
  and `trace`, so it cannot be used from a plain script or another runner.

## 4. Questions this raises for cot.tmppath

Not decisions, only what the above suggests a design must answer:

1. Separate *where the root is* from *how runs are numbered and retained*, so
   a custom root keeps retention (#10829).
2. Scope retention per project (for example a project key in the path), not
   per user.
3. Decide retention from the item's whole outcome (setup, call, teardown), not
   from `call` alone.
4. Offer a first-class "shared dir for this run" that xdist and other
   multi-process runners can use, instead of `getbasetemp().parent`.
5. Make deletion failures visible (a warning or a report entry), not silent.
6. Keep the security properties pytest learned the hard way: 0o700, ownership
   check, no symlink following at the predictable root, refuse dangerous
   basetemps.
7. Decide whether readable, stable directory names (full node id, hashed when
   too long) are worth more than the current numbered truncation, and whether
   `*current` symlinks are needed at all.
8. Keep a core with no pytest dependency, with pytest as one binding, in line
   with the other `cot` libraries.
