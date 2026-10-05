"""G5: concurrency- and crash-safe."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from conftest import not_built, run_python
from cot.tmppath import Retention, Root

pytestmark = not_built


def test_another_process_can_join_a_run(root_path: Path) -> None:
    with Root(root_path).start_run() as run:
        made = run_python(
            f"""
            from pathlib import Path
            from cot.tmppath import Root
            with Root(Path({str(root_path)!r})).join_run({run.id!r}) as run:
                print(run.item("from_worker"))
            """
        ).strip()
        assert Path(made).parent == run.path


def test_concurrent_processes_never_share_an_item(root_path: Path) -> None:
    with Root(root_path).start_run() as run:
        code = textwrap.dedent(
            f"""
            from pathlib import Path
            from cot.tmppath import Root
            with Root(Path({str(root_path)!r})).join_run({run.id!r}) as run:
                for _ in range(50):
                    print(run.item("test_same"))
            """
        )
        workers = [
            subprocess.Popen(
                [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True
            )
            for _ in range(4)
        ]
        made = [line for w in workers for line in w.communicate()[0].split()]
        assert all(w.returncode == 0 for w in workers)
    assert len(made) == 200
    assert len(set(made)) == 200


def test_live_run_survives_a_prune(root_path: Path) -> None:
    with Root(root_path).start_run() as run:
        Root(root_path, retention=Retention(keep_runs=0)).prune()
        assert run.path.is_dir()


def test_crashed_owner_run_is_collected(root_path: Path) -> None:
    Root(root_path)  # fail here, not in the child, while the core is a stub
    crashed = run_python(
        f"""
        import os
        from pathlib import Path
        from cot.tmppath import Root
        run = Root(Path({str(root_path)!r})).start_run()
        print(run.path, flush=True)
        os._exit(0)
        """
    ).strip()
    Root(root_path, retention=Retention(keep_runs=0)).prune()
    assert not Path(crashed).exists()


def test_place_is_atomic(root_path: Path) -> None:
    destination = root_path.parent / "cache" / "pkg.whl"
    destination.parent.mkdir()
    with Root(root_path).start_run() as run:

        def broken_transfer() -> None:
            with run.place(destination) as staging:
                staging.write_bytes(b"half")
                raise RuntimeError("transfer broke")

        with pytest.raises(RuntimeError):
            broken_transfer()
        assert not destination.exists()
        with run.place(destination) as staging:
            staging.write_bytes(b"whole")
    assert destination.read_bytes() == b"whole"
    assert list(destination.parent.iterdir()) == [destination]
