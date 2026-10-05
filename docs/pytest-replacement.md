# Running pytest with its tmp fixtures replaced

*Written by Claude via Claude Code from Ronny's brief; Ronny prompted it.*

Status: findings, and the opt-in plugin they led to. This is how pytest can
run with `tmp_path`, `tmp_path_factory`, `tmpdir` and `tmpdir_factory`
replaced by fixtures backed by cot.tmppath, and what a replacement has to
take care of.

Everything marked **(verified)** was run against pytest 9.1.1 and
pytest-xdist 3.8, and is pinned by `testing/test_pytest_replacement.py` with a
stand-in factory, so a pytest or xdist release that breaks it fails CI.

## How pytest wires its tmp fixtures

- The `tmpdir` plugin (`_pytest/tmpdir.py`) is a *default* plugin, not an
  essential one, so `-p no:tmpdir` disables it. It owns `tmp_path`,
  `tmp_path_factory`, the `tmp_path_retention_*` ini options, and the
  private `config._tmp_path_factory`.
- `--basetemp` is not the tmpdir plugin's. `_pytest/main.py` defines it, along
  with `validate_basetemp`, so it survives `-p no:tmpdir`.
- `legacypath` registers `tmpdir` and `tmpdir_factory` only if
  `pluginmanager.has_plugin("tmpdir")`. With the tmpdir plugin blocked, those
  two fixtures disappear as well.
- `pytester` asks for the `tmp_path_factory` *fixture* and only calls
  `mktemp(name, numbered=True)` on it. Its type annotation says
  `TempPathFactory`, but a duck-typed factory works **(verified)**.
- pytest-xdist's controller does
  `if hasattr(config, "_tmp_path_factory"): basetemp = config._tmp_path_factory.getbasetemp()`
  and passes `--basetemp={base}/popen-gwN` to each popen worker.

## The two ways to replace them

### A. Block the tmpdir plugin and provide all four fixtures (recommended)

```text
pytest -p no:tmpdir -p your_replacement
```

or permanently, in the project's configuration:

```ini
[pytest]
addopts = -p no:tmpdir -p your_replacement
```

The replacement plugin defines `tmp_path`, `tmp_path_factory`, `tmpdir` and
`tmpdir_factory`. All four tests pass, including `pytester`, with and without
xdist **(verified)**, and pytest creates nothing under `pytest-of-{user}`
**(verified)**.

### B. Leave it loaded and override the fixtures

A plugin loaded with `-p` (or a `conftest.py`) defines the same fixture names,
and its definitions win over the builtin ones **(verified)**. This needs no
blocking, but pytest's machinery stays half alive: under xdist the controller
still calls `config._tmp_path_factory.getbasetemp()`, which creates
`pytest-of-{user}/pytest-{N}` and starts pytest's own retention cleanup in
that folder **(verified)**. B is only a fallback for setups that cannot pass
`-p no:tmpdir`.

Unregistering the plugin from a `conftest.py`
(`config.pluginmanager.unregister(name="tmpdir")` in `pytest_configure`)
runs without error, but by then the tmpdir plugin has already added its ini
options and may already have configured itself, depending on hook order. It
is not worth relying on.

## What a replacement must take care of

1. **All four fixtures.** With A, `tmpdir` and `tmpdir_factory` vanish too,
   so the replacement provides them (`_pytest.compat.legacy_path` wraps a
   `Path`), or deliberately leaves them out to push a suite off `py.path`.
2. **`tmp_path_factory` duck type.** `mktemp(basename, numbered=True)` and
   `getbasetemp()`, as pytester and existing plugins and conftests call them.
3. **`--basetemp`.** It is still parsed and validated by pytest. The
   replacement reads `config.option.basetemp` and treats it as a new folder
   to create: an existing path is refused, because pytest's `--basetemp`
   deletes whatever is there. Nothing under a `--basetemp` is ever deleted,
   whatever the retention settings say; cleaning it up is the user's job.
   xdist hands workers the controller's options, so workers skip the check.
4. **xdist workers join the controller's run.** With A, xdist's `hasattr`
   check fails silently and workers get no basetemp at all. The replacement
   implements `pytest_configure_node` (as an `optionalhook`) to put the run's
   id in `node.workerinput`, and each worker's `pytest_configure` joins that
   run **(verified with a stand-in)**. This replaces the `getbasetemp().parent`
   convention with the run's shared folder.
5. **Retention from the whole outcome.** A function-scoped fixture's teardown
   runs *before* pytest builds the teardown report, so the fixture cannot know
   the final outcome. That is why pytest only looks at `call`. The replacement
   decides in `pytest_runtest_logreport` (or a `pytest_runtest_makereport`
   wrapper) once the `teardown` report exists, combining setup, call and
   teardown, and calls `Run.finish_item` then.
6. **`--strict-config`.** With A, `tmp_path_retention_count` and
   `tmp_path_retention_policy` become unknown ini options, which
   `--strict-config` turns into an error **(verified)**. The replacement
   registers both names itself (they are free once the tmpdir plugin is
   blocked) and maps them onto `Retention`, so existing configurations keep
   working.
7. **Layout.** Users who rely on paths like `pytest-of-{user}/pytest-{N}` get
   them from `PytestLayout`. Everyone else gets the flat layout.

## The opt-in plugin: `cot.tmppath.overtake_pytest`

A project opts in with one line:

```ini
[pytest]
addopts = -p cot.tmppath.overtake_pytest
```

It is a module in the package, not a `pytest11` entry point, so installing
cot.tmppath changes nothing until a project asks for it. It does everything
above:

- In its `pytest_addoption` it unregisters the `tmpdir` plugin and blocks the
  name, so `-p no:tmpdir` is not needed. A plugin given with `-p` is loaded
  while the command line is pre-parsed, before any `pytest_configure`, so the
  tmpdir plugin never configures itself, `config._tmp_path_factory` never
  exists, and legacypath skips its `tmpdir` fixtures **(verified)**.
- The tmpdir plugin's ini options were already registered by then, so
  `tmp_path_retention_count` and `tmp_path_retention_policy` stay valid under
  `--strict-config`. If the user also passes `-p no:tmpdir`, the plugin
  registers both itself **(verified)**.
- It provides all four fixtures. `tmpdir_factory` is the same object as
  `tmp_path_factory`, which offers `mktemp` and `getbasetemp`.
- The run is started on first use, not at configure time, so a session that
  asks for no temporary folder creates nothing **(verified)**.
- `--basetemp` must name a folder that does not exist yet; the plugin
  refuses an existing one with a usage error **(verified)**, creates the new
  one, and deletes nothing under it (`KEEP_EVERYTHING`).
- Without `--basetemp`, the root is `Root.for_project(rootdir name)`, and
  pytest's retention settings map onto `Retention`: `failed` keeps only
  failed items, `none` keeps no runs.
- **An existing folder that is empty and less than 10 seconds old** is used
  with a `PytestWarning` instead of refused: it was made for this run by
  whoever started pytest, and holds nothing to lose. This is what
  `pytester.runpytest_subprocess` does: it always creates the `--basetemp`
  folder just before starting pytest **(verified)**.
- **Intentional break:** `pytester.runpytest` passes the same `--basetemp`
  on every call in a test, so from the second call on the folder is neither
  empty nor fresh and the run is refused. Tests that rely on that, and any
  other caller that reuses a `--basetemp`, break on purpose; they must pass
  a new path.
- xdist workers get the root and run id through `pytest_configure_node` and
  join the controller's run.
- An item's fate is decided at its `teardown` report from setup, call and
  teardown together.
- Folders that could not be removed at the end are reported as
  `PytestWarning`s.

`testing/test_overtake_pytest.py` checks the takeover now and the behaviour
once the core is built.
