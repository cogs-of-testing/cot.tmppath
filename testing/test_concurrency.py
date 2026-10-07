"""G5: concurrency- and crash-safe."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from conftest import run_python
from cot.tmppath import Retention, Root
from cot.tmppath._owner import Owner


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


@pytest.mark.skipif(
    not Owner.current().started, reason="no process start time on this platform"
)
def test_reused_pid_does_not_keep_a_run_alive(root_path: Path) -> None:
    run = Root(root_path).start_run()
    # what a crashed holder looks like once its pid went to another process
    here = Owner.current()
    reused = Owner(here.pid, here.host, here.boot, started="0")
    (holder,) = (run.path / ".cot-holders").iterdir()
    holder.write_bytes(reused.to_bytes())
    Root(root_path, retention=Retention(keep_runs=0)).prune()
    assert not run.path.exists()
