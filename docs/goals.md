# Project goals

*Written by Claude via Claude Code from Ronny's brief; Ronny prompted it.*

Status: draft for review. Anything marked **open** is a question, not a
decision. The background is in [research.md](research.md).

## What cot.tmppath is

A hardened building block for **creating, finding and cleaning up related
temporary folders**. You ask for a folder in a tree of related folders
(a run, a group inside it, one item), and the library makes it safely, names
it predictably, keeps or removes it by policy, and copes with other
processes doing the same thing at the same time.

It has two first users, and neither of them is privileged in the core:

- **Test runners.** A pytest binding offers what `tmp_path`,
  `tmp_path_factory` and `--basetemp` offer today, without the problems listed
  in the research.
- **cot.runsomewhere.** Workers on a target need scratch and staging folders
  while bootstrapping and deploying: wheels being received, environments
  being built, per-worker scratch space. These folders must work on machines
  runsomewhere has never seen, under users it does not control, and survive
  crashes of the process that made them.

## Goals

### G1. Hardened by default

Safe on a shared `/tmp`, on an unfamiliar ssh target and in a container,
with no setting needed.

- Folders are private (0o700) and owned by the current user. The library
  checks this and does not just assume it.
- At the predictable, shared levels of the tree, it never follows a symlink.
  It works through directory file descriptors (`dir_fd`, `O_NOFOLLOW`) where
  the platform has them, so a path that is checked is the path that gets used.
  This covers the class of attack behind
  [CVE-2025-71176](https://github.com/pytest-dev/pytest/issues/14343) and
  [#8414](https://github.com/pytest-dev/pytest/issues/8414).
- It only ever deletes what it created. A folder it made carries a marker,
  and a root without the marker is refused, not wiped. This is unlike
  `--basetemp`, which today deletes any directory it is given.
- A planted directory or a hostile pre-existing root is an error that names
  the path and the fix. It is never silently reused.

### G2. Related folders, one model

One tree with named levels instead of a flat base dir plus `mktemp`:

```text
root  ─►  project  ─►  run  ─►  group  ─►  item
```

- **project** keeps one project's runs apart from another's, so retention
  never deletes another project's runs (in pytest today, every project
  shares one `pytest-of-{user}` counter).
- **run** is one invocation. Several processes can join the same run
  (xdist-style workers, runsomewhere workers), and the run has a first-class
  shared folder. That replaces the `getbasetemp().parent` convention.
- **group** and **item** are what a host maps its scopes onto: a module and
  a test for pytest, a deployment and a worker for runsomewhere.
- Names are stable and readable, derived from what the caller names, and
  unique without scanning the directory (see G3). Over-long names are
  shortened deterministically, so the same item gets the same name in every
  run.

### G3. Faster than pytest's implementation

Time is a goal, not an afterthought, and it is measured.

- **No directory scans on the hot path.** pytest lists the whole base folder
  on every `mktemp` to find the next number, which is O(n²) over a run. Here,
  making an item folder costs about one `mkdir`, whatever its siblings.
- **No `resolve()` per folder.** The root is resolved and validated once, and
  everything below it is built from known-good parts.
- **Lazy.** A folder that is never asked for is never created, and nothing
  touches the disk at import or configure time.
- **Deletion off the hot path.** Removing a folder is a rename into a trash
  area, and the actual delete happens at run end, in the background, or in a
  later run. A test's teardown never waits on `rmtree`.
- **Bounded cleanup.** Pruning old runs costs time in proportion to the
  number of runs, not the number of files in them, until something is
  actually deleted.
- **Benchmarks in CI** against pytest's `tmp_path` for many small tests,
  deep trees and many concurrent processes. A regression fails the build.

### G4. Retention that is correct and explicit

- Keep-or-remove is decided from an item's **whole outcome**, which a host
  reports. For pytest that means setup, call and teardown, not `call` alone,
  which today discards the folders of tests that error in setup or teardown.
- Retention works with any root, including a custom one, which `--basetemp`
  cannot do ([#10829](https://github.com/pytest-dev/pytest/issues/10829)).
- Policies cover count and age, so runsomewhere can prune workspaces unused
  for a given time.
- A folder that could not be removed is reported as a result the caller can
  read, never swallowed.

### G5. Concurrency- and crash-safe

- Any number of processes, on one host or sharing a filesystem, can create,
  join and prune runs at the same time without corrupting or deleting each
  other's live folders.
- A folder's liveness comes from a lock tied to its owner (pid, host and boot
  identity), so a crashed owner's folders become collectable as soon as it is
  known to be dead. pytest instead waits three days, or deletes a long run
  that is still alive.
- It provides **atomic placement**: write under a temporary name in the same
  folder, then rename into place. runsomewhere's wheel cache and file
  transfer need exactly this, and today each would write it again.

### G6. A core with no host

- The core imports no test runner and knows no host. pytest is a binding, in
  the same split as cot.config.ingest. runsomewhere uses the core directly.
- **Pure Python, no runtime dependencies.** runsomewhere ships its runtime
  dependencies as wheels during bootstrap and requires them to be pure
  Python, so this is a hard constraint, not a preference.
- Python 3.10+, matching the other cot packages.
- Linux, macOS and Windows. Windows needs no symlink privilege, and long
  paths work for creation as well as deletion.
- The library never prints and never exits. It returns results and raises
  typed errors, and the application owns the output.

## Non-goals

- Single temporary files. `tempfile` already does that well, and the library
  only places files atomically inside folders it manages.
- Secure erasure of data on deletion.
- The shell-only "no Python at all" bootstrap rung in runsomewhere, which
  runs before any Python exists.
- Guarantees on network filesystems where locking or rename atomicity is
  unreliable. Such a root is detected where possible and reported, not
  supported. **(open)**

## How we will know it works

- The pytest binding passes a port of pytest's own `testing/test_tmpdir.py`,
  with the cases the research lists as wrong changed to the new behaviour.
- Attack tests cover a planted symlink, a foreign-owned root, a world-writable
  root and a pre-existing non-marked `--basetemp`.
- The benchmarks beat pytest's `tmp_path` on every scenario in G3.
- runsomewhere's bootstrap cache, transfer staging and workspaces use it,
  with no temporary-folder code of their own.

## Open questions

1. **Persistent folders.** Should cot.tmppath also own runsomewhere's
   long-lived workspaces and caches (`XDG_DATA_HOME`, `XDG_CACHE_HOME`, prune
   by unused time), or only folders that are temporary by nature?
2. **pytest layout compatibility.** Keep `pytest-of-{user}/pytest-{N}` so
   existing `getbasetemp().parent` users and xdist keep working, or use the
   new layout and give xdist the shared run folder instead?
3. **Network filesystems.** Refuse them, warn, or support them with weaker
   guarantees?
