# Changelog

<!-- towncrier release notes start -->

## 0.4.0 (2026-10-08)

### Added

- The default root is now the per-user folder `{temp}/cot-{uid}`, and runs are named `{project}-{date}-{time}-{random}`, so an item sits two levels below the temp folder, as with pytest, instead of three. Retention and `Root.all_projects()` go by the project recorded in each run. Runs under the old `cot.tmppath-{uid}/{project}` folders are no longer pruned; remove that folder by hand.

### Fixed

- `-p no:cot.tmppath.overtake_pytest` after an opt-in (for example on the command line of a project that opts in through `addopts`) now leaves pytest's own `tmp_path`, `tmp_path_factory`, `tmpdir` and `tmpdir_factory` working; before, `tmp_path` failed with `AttributeError: 'Config' object has no attribute '_tmp_path_factory'` and `tmpdir` was missing. The plugin now takes over in `pytest_load_initial_conftests`, after every `-p` is processed, and registers its fixtures in `pytest_configure`. Loading it from a conftest's `pytest_plugins` is too late for that and is now refused with a usage error; enable it with `-p`.
- `python -m cot.tmppath prune ROOT` no longer applies the default retention (keep 3 runs), which it cannot know a root was made with: a root made with `KEEP_EVERYTHING` lost all but its 3 newest runs. It now removes only what it is asked to: runs unused for `--older-than`, all but the N newest runs with the new `--keep N`, and interrupted removals. `Root.all_projects()` takes a `retention`.

### Documentation

- New `docs/examples.md`: worked examples of the library (runs, items, retention, refused roots, several processes, crashes, atomic placement, layouts, `prune`) and of the pytest binding next to plain pytest (folder layout, which failures keep their folder, `--basetemp`, pytest-xdist, per-project runs, `mktemp(numbered=False)`, `tmpdir`). `testing/test_examples.py` runs every example and compares the folder trees on the page with what pytest leaves on disk.
- `docs/pytest-replacement.md` no longer says the plugin replaces `tmpdir` and `tmpdir_factory` (it does not supply them) or that xdist drops the `getbasetemp().parent` convention (it is kept), and describes the opt-out; `docs/assessment.md` marks its summary as the state before the core was built; test and benchmark docstrings no longer mention xfail tests.

## 0.3.0 (2026-10-08)

### Added

- The pytest binding replaces pytest's basetemp handling as a whole: `getbasetemp()` is the run folder (an xdist worker's own folder in the run), `tmp_path` and `mktemp()` folders are made inside it, and `config._tmp_path_factory` is the cot.tmppath factory. `--basetemp` names a root that holds the runs; it is reused when cot.tmppath made it, so repeated `pytester.runpytest()` calls work, and a folder cot.tmppath did not make is refused and never touched. New: `Root.ensure()`, and `Run.item(name, process=...)`.

## 0.2.1 (2026-10-07)

### Fixed

- A crashed run whose pid was reused by another process is collected: holder files record the process start time on Linux and Windows. Before, the run counted as alive until that other process ended. macOS still relies on the pid alone.

## 0.2.0 (2026-10-07)

### Removed

- The pytest plugin no longer provides the `py.path` fixtures `tmpdir` and `tmpdir_factory`; use `tmp_path` and `tmp_path_factory`.

### Added

- Build the core: `Root`, `Run`, the flat and pytest layouts, retention, pruning, liveness and atomic placement now work instead of raising `NotImplementedError`, so `-p cot.tmppath.overtake_pytest` provides working `tmp_path` and `tmp_path_factory` fixtures.
