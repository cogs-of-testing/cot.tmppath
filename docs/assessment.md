# Assessment: hardening, pytest policies, cleanup

*Written by Claude via Claude Code from Ronny's brief; Ronny prompted it.*

Status as of 2026-10-05. Checked against `main`, pytest 9.1.1 and
pytest-xdist 3.8. **(verified)** means it was run, not read.

## Summary

- **The core implements no hardening yet.** Everything in `_api.py` raises
  `NotImplementedError`. What exists is a specification: 4 hardening tests
  in `testing/test_hardening.py`, all xfail. Today cot.tmppath is not safer
  than pytest, it is a plan to be.
- **The specification has real gaps** (below), the largest being that the
  default root has no per-user part, which reproduces pytest's shared-`/tmp`
  denial of service.
- **The pytest plugin is the only working code.** Its `--basetemp` check had
  a symlink hole, now fixed; three compatibility gaps were fixed, and three
  semantic differences from pytest remain open.
- **A cleanup command is worth having**, as a thin wrapper over
  `Root.prune`, mainly for runsomewhere targets and for projects that are no
  longer run. Recommendation below.

## 1. Hardening

### What the tests specify

| Property | Test | State |
|---|---|---|
| root, run and item folders are 0o700 | `test_folders_are_private` | xfail |
| a symlinked root is refused, target untouched | `test_symlinked_root_is_refused` | xfail |
| an existing folder we did not create is refused, not wiped | `test_existing_folder_it_did_not_create_is_refused_not_wiped` | xfail |
| a root owned by another user is refused | `test_root_owned_by_another_user_is_refused` | xfail, runs only as root |
| an existing `--basetemp` is refused, contents untouched | `test_existing_basetemp_is_refused` | **passes** |
| a symlink to a fresh empty folder is refused as `--basetemp` | `test_symlink_to_a_fresh_empty_folder_is_refused` | **passes** (fixed today) |

### Gaps in the specification

These are not covered by any test and not decided in `goals.md`:

1. **No per-user part in the default root** (decided: added, as
   `{temproot}/cot.tmppath-{user}/{project}`, see goals G2). `Root.for_project(project)`
   puts the root under the system temp folder by project name alone. On a
   shared `/tmp`, user B pre-creating `/tmp/<project>` makes user A's runs
   fail the ownership check: the same denial of service pytest accepts for
   `pytest-of-{user}`. The default root needs the user in its name (uid on
   POSIX), and the parent should be checked for the sticky bit when it is
   world-writable.
2. **TOCTOU between check and use is untested.** G1 promises `dir_fd` and
   `O_NOFOLLOW`, but no test swaps a folder for a symlink between the check
   and the `mkdir` of an item, or replaces a run folder while a run is live.
3. **The plugin's `--basetemp` check is advisory.** It runs at configure
   time; the folder is created on first use. Only the core creating the root
   with an exclusive `mkdir` (and refusing on `EEXIST` unless it is the
   accepted fresh folder) closes that window.
4. **Windows has no equivalent of 0o700.** `chmod` there only toggles the
   read-only flag. Protection rests on `%TEMP%` being per-user. Not stated in
   the goals, not tested; a root outside the user profile is unprotected.
5. **Lock liveness is unspecified at the edge cases**: pid reuse after a
   reboot (hence the boot id in G5), and a lock on a filesystem shared
   between hosts.
6. **Read-only content.** Tests that `chmod` their files read-only made pytest
   crash on the next run (pytest #5524). No test covers removal of read-only
   files and folders, or of folders a test made unreadable.
7. **The marker is unspecified.** "Only deletes what it created" depends on a
   marker; its content, how it is checked, and that it cannot be planted by
   another user (the ownership check covers that on POSIX) need writing down.

## 2. pytest's policies and our mapping

What pytest does, observed with `count=2` over five runs with one passing and
one failing test, then one all-passing run **(verified)**:

| Policy | pytest keeps | All-passing run | Our mapping |
|---|---|---|---|
| `all` | last *count* runs, every test folder | kept, counts as a run | `keep_runs=count` |
| `failed` | last *count* runs, only failed test folders | whole run folder removed at session end | `keep_failed_only=True, keep_runs=count` |
| `none` | nothing, not even the current run after it ends | removed | `keep_runs=0` |

Where we differ:

1. **`failed`: an all-passing run leaves an empty run folder.** pytest
   removes it. We should remove a run that ends with no items, or the empty
   folders pile up to *count*. Small; belongs in the core's `close()`.
2. **`none`: whether `keep_runs=0` removes the current run at close is not
   specified.** pytest does. Our concurrency test only says a *live* run
   survives another process's prune. A test should pin that closing with
   `keep_runs=0` removes the run.
3. **Whole outcome, not `call`.** Deliberately different: we keep folders of
   setup and teardown failures, pytest discards them
   (`test_failed_policy_keeps_setup_and_teardown_failures`).
4. **`--basetemp`.** Deliberately different: no wiping, no retention, refuse
   an existing folder (issue #1 tracks the option's name).

Plugin compatibility, checked against pytest's `tmpdir.py` and
`legacypath.py`:

| pytest behaviour | Plugin | State |
|---|---|---|
| `tmpdir_factory` returns `py.path` objects | returned `Path` | **fixed today** |
| `PYTEST_DEBUG_TEMPROOT` overrides the temp root | ignored | **fixed today** |
| retention ini values validated, error on bad value | `UsageError` | ok |
| `mktemp(name, numbered=False)` creates exactly `name`, fails if it exists | always unique name | **open**: breaks code that relies on the exact name |
| `getbasetemp()` is per process; under xdist `getbasetemp().parent` is the shared run folder | the process's own folder, named by the controller | **fixed**: `getbasetemp().parent` is the run |
| `mktemp` rejects absolute and non-normalised names (#5686) | passed to `Run.item`, which flattens them | **open**: decide whether to reject like pytest |
| `tmp_path` is a resolved real path (#4653) | depends on the core | open, add to the core tests |
| test folder names truncated to 30 characters | full sanitized name, the core shortens to at most 64 | intended |

`getbasetemp()` now returns a per-process folder inside the run, named by
the manager (`Run.process_folder`), so `getbasetemp().parent` means "the
run" as it does with pytest under xdist.

## 3. Do we need a cleanup command?

**Who cleans up today.** pytest removes old runs only when the same user runs
pytest again, and only in its own `pytest-of-{user}`. Our retention is the
same: it runs when a run closes, per project. So nothing ever removes:

- the runs of a project that is not run again;
- the runs left by crashed processes, until that project runs again and the
  owner is known dead;
- runsomewhere's staging and scratch on a target host that is not visited
  again, which the system's `/tmp` cleaning may or may not catch
  (from memory, not checked here: systemd-tmpfiles typically ages `/tmp`
  after about 10 days, macOS cleans `/var/folders` after a few days, and
  containers and long-lived CI runners often never clean at all).

`Root.prune()` already exists in the API, so a command is a thin wrapper.

**Decided: yes, small.** Built as `python -m cot.tmppath prune` (also the
`cot-tmppath` script):

```text
python -m cot.tmppath prune [ROOT ...] [--all-projects] [--older-than 7d]
                            [--dry-run | --delete-without-asking-i-have-read-the-dry-run]
```

- By default it lists what it would remove and asks; only typing `yes`
  removes anything. Without a terminal to ask on, it refuses.
- `--dry-run` only lists.
- Removing without asking takes a flag that is long on purpose, and
  argparse abbreviations are off, so `--delete` does not expand to it
  **(verified: argparse accepts such prefixes by default)**.
- It removes exactly the listed folders, each checked again first
  (`PrunePlan.apply`).

- It applies exactly the library's rules: only folders carrying our marker,
  owned by the current user, whose lock owner is dead; never anything else.
- `--all-projects` walks every project root under the user's default
  location, which is the case retention cannot reach.
- runsomewhere can call `Root.prune` directly over its own connection; the
  command is for people and cron.
- A pytest option (`--tmp-prune`) is not needed: the plugin prunes the
  current project on every run already.
