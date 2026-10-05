"""G1: hardened by default."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from conftest import not_built, posix_only
from cot.tmppath import Root, UnsafeRootError

pytestmark = not_built


@posix_only
def test_folders_are_private(root_path: Path) -> None:
    with Root(root_path).start_run() as run:
        item = run.item("x")
        for folder in (root_path, run.path, item):
            assert folder.stat().st_mode & 0o077 == 0, folder


def test_symlinked_root_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "root"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks here")
    with pytest.raises(UnsafeRootError):
        Root(link).start_run()
    assert list(target.iterdir()) == []


def test_existing_folder_it_did_not_create_is_refused_not_wiped(
    root_path: Path,
) -> None:
    root_path.mkdir()
    precious = root_path / "precious.txt"
    precious.write_text("keep me")
    with pytest.raises(UnsafeRootError):
        Root(root_path).start_run()
    assert precious.read_text() == "keep me"


@posix_only
@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() != 0, reason="needs root to chown"
)
def test_root_owned_by_another_user_is_refused(root_path: Path) -> None:
    root_path.mkdir(mode=0o700)
    os.chown(root_path, 65534, 65534)
    with pytest.raises(UnsafeRootError):
        Root(root_path).start_run()
