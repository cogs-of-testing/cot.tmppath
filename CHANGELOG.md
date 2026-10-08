# Changelog

<!-- towncrier release notes start -->

## 0.2.1 (2026-10-07)

### Fixed

- A crashed run whose pid was reused by another process is collected: holder files record the process start time on Linux and Windows. Before, the run counted as alive until that other process ended. macOS still relies on the pid alone.

## 0.2.0 (2026-10-07)

### Removed

- The pytest plugin no longer provides the `py.path` fixtures `tmpdir` and `tmpdir_factory`; use `tmp_path` and `tmp_path_factory`.

### Added

- Build the core: `Root`, `Run`, the flat and pytest layouts, retention, pruning, liveness and atomic placement now work instead of raising `NotImplementedError`, so `-p cot.tmppath.overtake_pytest` provides working `tmp_path` and `tmp_path_factory` fixtures.
