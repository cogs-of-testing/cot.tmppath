# Changelog

<!-- towncrier release notes start -->

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
