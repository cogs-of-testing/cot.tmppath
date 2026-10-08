# Project goals

*Written by Claude via Claude Code from Ronny's brief; Ronny prompted it.*

Status: draft for review. Anything marked **open** is a question, not a
decision. The background is in [research.md](research.md).

## What cot.tmppath is

A hardened building block for **creating, finding and cleaning up related
temporary folders**. Each run gets its own folder, and everything the run
needs lives directly inside it. The library makes those folders safely,
names them predictably, keeps or removes them by policy, and copes with
other processes doing the same thing at the same time.

Everything it manages is **temporary**: it belongs to a run and goes away
by policy. Long-lived workspaces and caches are out of scope.

It has two first users, and neither of them is privileged in the core:

- **Test runners.** There is no automatic pytest binding. A project opts in
  with `addopts = -p cot.tmppath.overtake_pytest`, which replaces pytest's
  `tmp_path` and `tmp_path_factory` with fixtures backed by cot.tmppath
  (the `py.path` fixtures `tmpdir` and `tmpdir_factory` are not provided), without the problems listed in the research
  ([pytest-replacement.md](pytest-replacement.md)).
- **cot.runsomewhere.** Workers on a target need scratch and staging folders
  while bootstrapping and deploying: wheels being received before they move
  into runsomewhere's own cache, per-worker scratch space. These folders must
  work on machines runsomewhere has never seen, under users it does not
  control, and be cleaned up after a crash of the process that made them.

## Goals

### G1. Hardened by default

Safe on a shared `/tmp`, on an unfamiliar ssh target and in a container,
with no setting needed.

- Folders are private (0o700) and owned by the current user. The library
  checks this and does not just assume it.
- At the predictable, shared levels, it never follows a symlink. It works
  through directory file descriptors (`dir_fd`, `O_NOFOLLOW`) where the
  platform has them, so a path that is checked is the path that gets used.
  This covers the class of attack behind
  [CVE-2025-71176](https://github.com/pytest-dev/pytest/issues/14343) and
  [#8414](https://github.com/pytest-dev/pytest/issues/8414).
- It only ever deletes what it created. A folder it made carries a marker,
  and a root without the marker is refused, not wiped. This is unlike
  `--basetemp`, which today deletes any directory it is given.
- A planted directory or a hostile pre-existing root is an error that names
  the path and the fix. It is never silently reused.

### G2. One folder per run, flat inside

The starting point is deliberately simple:

```text
{root}/{run}/{item}
```

- **run** is one invocation. Several processes can join the same run
  (xdist-style workers, runsomewhere workers) and share its folder. Under
  pytest-xdist that keeps the `getbasetemp().parent` convention: a worker's
  `getbasetemp()` is its process folder, and its parent is the run.
- **process folders** (`Run.process_folder(name)`) give each process of a
  run its own folder directly in the run. The manager of the processes
  picks the names (the xdist controller names `gw0`, `gw1`, ...), never the
  processes themselves.
- **The default root is per user:** `{temproot}/cot-{user}`, a private,
  owner-checked folder, so another user cannot block or read a project's
  runs by creating it first. The project is not a folder level: it starts
  the run's name (`{project}-{date}-{time}-{random}`) and is recorded in the
  run, so an item sits two levels below the temp folder, as with pytest.
- **item** folders sit directly in the run folder, side by side, whatever
  the item is: a test, a module fixture's data, a worker's scratch, a staging
  area. Grouping is expressed in the item's name, not in extra levels.
- Depth stays fixed and short, because every extra level eats into
  Windows' 260-character path limit before a test writes anything.
- Names are readable, derived from what the caller names, and unique without
  scanning the directory (see G3). Over-long names are shortened
  deterministically, so the same item gets the same name in every run.
- **Layouts are policies.** The default is the flat layout above. pytest's
  `pytest-of-{user}/pytest-{N}/{testname}{M}` layout is shipped as an
  example policy, which also shows how a host plugs in its own.

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
- Retention works with any root a caller gives the library, which
  `--basetemp` cannot do
  ([#10829](https://github.com/pytest-dev/pytest/issues/10829)). pytest's
  `--basetemp` becomes such a root: it holds the runs, and only folders
  cot.tmppath made there are ever removed.
- Retention is kept per project, so one project's runs never push out
  another's (in pytest today, every project shares one `pytest-of-{user}`
  counter).
- Policies cover run count and age.
- A folder that could not be removed is reported as a result the caller can
  read, never swallowed.

### G5. Concurrency- and crash-safe

- Any number of processes on one host can create, join and prune runs at
  the same time without corrupting or deleting each other's live folders.
- A run's liveness comes from a lock tied to its owner (pid, host and boot
  identity), so a crashed owner's run becomes collectable as soon as it is
  known to be dead. pytest instead waits three days, or deletes a long run
  that is still alive.
- It provides **atomic placement**: write under a temporary name in a
  managed folder, then rename into the destination. runsomewhere's wheel
  transfer needs exactly this, and today it would write it again.

### G6. A core with no host

- The core imports no test runner and knows no host. pytest runs with its
  tmp fixtures replaced (G1 to G5 apply there too), and runsomewhere uses the
  core directly.
- **Pure Python, no runtime dependencies.** runsomewhere ships its runtime
  dependencies as wheels during bootstrap and requires them to be pure
  Python, so this is a hard constraint, not a preference.
- Python 3.10+, matching the other cot packages.
- Linux, macOS and Windows. Windows needs no symlink privilege, and long
  paths work for creation as well as deletion.
- The library never prints and never exits. It returns results and raises
  typed errors, and the application owns the output.

## Non-goals

- Long-lived folders: runsomewhere's workspaces, wheel and interpreter
  caches stay runsomewhere's own.
- Single temporary files. `tempfile` already does that well, and the library
  only places files atomically inside folders it manages.
- Secure erasure of data on deletion.
- The shell-only "no Python at all" bootstrap rung in runsomewhere, which
  runs before any Python exists.

## How we will know it works

- pytest, with its tmp fixtures replaced, passes a port of pytest's own
  `testing/test_tmpdir.py`, with the cases the research lists as wrong
  changed to the new behaviour.
- Attack tests cover a planted symlink, a foreign-owned root, a world-writable
  root and a pre-existing non-marked `--basetemp`.
- The benchmarks beat pytest's `tmp_path` on every scenario in G3.
- runsomewhere's bootstrap staging and worker scratch use it, with no
  temporary-folder code of their own.

## Open questions

1. **Network filesystems (deferred).** Research is deferred. The leaning is to refuse
   a root on a network filesystem by default and allow it only by explicit
   opt-in. Before deciding, research which guarantees break there (locking,
   rename atomicity, ownership and permissions over NFS, SMB and container
   bind mounts) and how reliably such a root can be detected on each platform.
