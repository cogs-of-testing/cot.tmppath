# How the core works

*Written by Claude via Claude Code from Ronny's brief; Ronny prompted it.*

What `src/cot/tmppath/_api.py` does to keep the goals in
[goals.md](goals.md). `testing/` pins each point.

## On disk

```text
{root}/
  .cot-tmppath                 marker: this root was made by cot.tmppath
  run-20261007-161500-a1b2c3/  one folder per run
    .cot-run                   the run's start time
    .cot-holders/{pid}-{token} one file per process using the run
    .cot-trash-{pid}-{token}/  removed items of one process, until it closes
    test_foo/                  items, side by side
    test_foo-1/
    gw0/                       process folders (one per xdist worker)
      test_bar/                items made with process="gw0"
```

Every name starting with `.cot-` is the library's; item names never do.
The default root adds two levels above this:
`{temproot}/cot.tmppath-{uid}/{project}`.

## Hardening (G1)

- Each level the library owns (the per-user folder, the root, runs, items)
  is created with mode 0o700. An existing level must be a real folder (no
  symlink, no junction), owned by the current user, private, and carry the
  marker. Anything else raises `UnsafeRootError` naming the path and the
  fix, and nothing in it is touched.
- One exception: an **empty** folder owned by the current user without the
  marker is adopted (made private, marked). That is what
  `pytester.runpytest_subprocess` hands over as `--basetemp`; an empty
  folder holds nothing to lose, and one owned by another user is still
  refused.
- On Linux and macOS every operation below the checked base goes through
  directory descriptors (`dir_fd`, `O_NOFOLLOW`), so the folder that was
  checked is the folder that is used. Windows has no `dir_fd`; there the
  checks use `lstat` on paths.
- A prune removes only folders with a run marker inside a marked root, and
  checks both again right before removing.

## Names (G2, G3)

- The flat layout names an item after what the caller passes: anything but
  `A-Z a-z 0-9 _ . -` becomes `_`, Windows device names get a `_` prefix,
  and names over 56 characters keep their first 47 and add 8 hex digits of
  a hash of the whole name, so the same item gets the same name in every
  run.
- A second item of the same name gets `-1`, `-2`, ... from a counter in the
  layout object. Making an item is one `mkdir` (more only when another
  process took the name), and never lists the run folder.
- Run names are `run-{date}-{time}-{random}`. Their order comes from the
  start time in `.cot-run`, not the name.

## Liveness and retention (G4, G5)

- A process that starts or joins a run writes a holder file with its pid,
  host, Linux boot id and process start time, and removes it when it closes
  the run. A run is live while any holder may be alive: same host and boot,
  the pid exists, and the process with that pid started when the holder
  says. A holder from another host always counts as alive, so a run is
  never collected on a guess.
- The start time comes from `/proc` on Linux and `GetProcessTimes` on
  Windows. **Open:** macOS has neither, so there a pid reused by an
  unrelated process keeps a crashed run alive until that process ends.
- Closing a run applies retention to the whole root: the newest
  `keep_runs` runs stay, older ones that are not live go, and so do runs
  older than `max_age`. The run being closed counts as the newest. A
  removed run is first renamed to `.cot-deleting-...`, then deleted; a
  removal that was interrupted is finished by the next prune.
- `keep_failed_only` moves a passed item into the process's trash at once
  (a rename) and deletes the trash when the run closes, so a test's
  teardown never waits on deletion.
- Failures to delete are returned in `PruneReport.failed`, never printed.

## Placement (G5)

`Run.place(destination)` yields a path inside a private staging folder of
the run. On success it is renamed onto `destination`; across filesystems
it is copied next to the destination first and renamed from there. On
error, and in every case afterwards, the staging folder is deleted.

## Measured

`uv run --group bench pytest benchmarks` on Linux (2026-10-07, mean):

| Scenario | pytest | cot.tmppath |
|---|---|---|
| 100 items | 48 ms | 6 ms |
| 1000 items | 771 ms | 49 ms |
| remove a 200-file item | 1.9 ms | 0.05 ms |
