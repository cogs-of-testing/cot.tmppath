"""``python -m cot.tmppath prune``: remove old runs, asking first.

This is the application side, so unlike the library it prints and exits.
It removes only what the library would: folders cot.tmppath created, owned
by the current user, whose runs are not live.

By default it lists what it would remove and asks for confirmation.
``--dry-run`` only lists. Removing without asking needs a flag that is long
on purpose, so nobody types it by accident or copies it without reading.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from ._api import Root

NO_CONFIRMATION_FLAG = "--delete-without-asking-i-have-read-the-dry-run"

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str) -> float:
    """``30s``, ``15m``, ``12h``, ``7d`` or ``2w`` in seconds."""
    match = re.fullmatch(r"(\d+)([smhdw])", text.strip())
    if match is None:
        msg = f"not a duration: {text!r} (use e.g. 30m, 12h, 7d)"
        raise argparse.ArgumentTypeError(msg)
    return int(match[1]) * _UNITS[match[2]]


def _parser() -> argparse.ArgumentParser:
    # no abbreviations: "--delete" must not expand to the long flag
    parser = argparse.ArgumentParser(prog="python -m cot.tmppath", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    prune = commands.add_parser(
        "prune", help="remove runs that retention no longer keeps", allow_abbrev=False
    )
    prune.add_argument("roots", nargs="*", type=Path, metavar="ROOT")
    prune.add_argument(
        "--all-projects",
        action="store_true",
        help="every project of the current user under the default location",
    )
    prune.add_argument(
        "--older-than",
        type=parse_duration,
        metavar="DURATION",
        help="also remove runs unused for this long (30m, 12h, 7d, 2w)",
    )
    mode = prune.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="only list what would be removed"
    )
    mode.add_argument(
        NO_CONFIRMATION_FLAG,
        dest="no_confirmation",
        action="store_true",
        help="remove without asking; for scripts, after checking --dry-run",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.roots and not args.all_projects:
        parser.error("name at least one ROOT, or pass --all-projects")
    asking = not (args.dry_run or args.no_confirmation)
    if asking and not stdin.isatty():
        parser.error(
            "cannot ask for confirmation, stdin is not a terminal;"
            f" use --dry-run, or {NO_CONFIRMATION_FLAG}"
        )

    roots = [Root(path) for path in args.roots]
    if args.all_projects:
        roots.extend(Root.all_projects())
    plans = [root.plan_prune(older_than=args.older_than) for root in roots]
    paths = [path for plan in plans for path in plan.paths]
    if not paths:
        print("nothing to remove", file=stdout)
        return 0
    for path in paths:
        print(path, file=stdout)
    if args.dry_run:
        print(f"{len(paths)} folders would be removed (dry run)", file=stdout)
        return 0
    if asking:
        print(f"remove these {len(paths)} folders? type 'yes': ", end="", file=stdout)
        stdout.flush()
        if stdin.readline().strip() != "yes":
            print("nothing removed", file=stdout)
            return 1

    failed = 0
    for plan in plans:
        report = plan.apply()
        for path, reason in report.failed:
            failed += 1
            print(f"could not remove {path}: {reason}", file=stdout)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
