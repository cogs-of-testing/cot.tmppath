from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Tests for behaviour that is not built yet carry this marker. With
# xfail_strict, a test that starts passing fails until the marker is removed,
# and a test failing with anything but NotImplementedError is a real failure.
not_built = pytest.mark.xfail(
    raises=NotImplementedError, reason="core not built yet (docs/goals.md)"
)

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission model"
)


def run_python(code: str) -> str:
    """Run ``code`` in a fresh interpreter and return its stdout."""
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def root_path(tmp_path: Path) -> Path:
    return tmp_path / "root"
