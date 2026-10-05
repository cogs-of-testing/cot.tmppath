"""cot.tmppath.overtake_pytest: opt-in takeover of pytest's tmp fixtures.

The mechanism tests pass already: the plugin creates its run lazily, so
loading it and handing out factories never touches the stubbed core. The
behaviour tests are xfail until the core is built.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

from conftest import not_built
from cot.tmppath import Root

pytest_plugins = ["pytester"]

PLUGIN = "cot.tmppath.overtake_pytest"


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    # not runpytest_subprocess: it always adds a --basetemp it has already
    # created, which the plugin refuses (and which disables retention)
    return pytester.run(sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args)


def test_addopts_opt_in_takes_over(pytester: pytest.Pytester) -> None:
    pytester.makeini(f"[pytest]\naddopts = -p {PLUGIN}\n")
    pytester.makepyfile(
        f"""
        def test_takeover(request, tmp_path_factory, tmpdir_factory):
            config = request.config
            assert not config.pluginmanager.has_plugin("tmpdir")
            assert not hasattr(config, "_tmp_path_factory")
            assert type(tmp_path_factory).__module__ == {PLUGIN!r}
            assert tmpdir_factory is tmp_path_factory
        """
    )
    _run(pytester).assert_outcomes(passed=1)


@pytest.mark.parametrize("block", [False, True], ids=["alone", "with-no-tmpdir"])
def test_retention_settings_stay_valid_under_strict_config(
    pytester: pytest.Pytester, block: bool
) -> None:
    pytester.makeini(
        "[pytest]\ntmp_path_retention_policy = failed\ntmp_path_retention_count = 2\n"
    )
    pytester.makepyfile("def test_nothing(): pass")
    extra = ["-p", "no:tmpdir"] if block else []
    _run(pytester, *extra, "-p", PLUGIN, "--strict-config").assert_outcomes(passed=1)


def test_nothing_is_created_until_a_folder_is_asked_for(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    temproot = pytester.mkdir("temproot")
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(temproot))
    pytester.makepyfile("def test_nothing(tmp_path_factory): pass")
    _run(pytester, "-p", PLUGIN).assert_outcomes(passed=1)
    assert list(temproot.iterdir()) == []


@not_built
def test_fixtures_live_in_the_run(pytester: pytest.Pytester) -> None:
    base = pytester.path / "base"
    Root(base)  # fail here, not in the child, while the core is a stub
    pytester.makepyfile(
        f"""
        pytest_plugins = ["pytester"]
        BASE = {str(base)!r}

        def test_tmp_path(tmp_path):
            assert str(tmp_path).startswith(BASE)

        def test_tmpdir(tmpdir):
            assert str(tmpdir).startswith(BASE)

        def test_pytester(pytester):
            assert str(pytester.path).startswith(BASE)
        """
    )
    _run(pytester, "-p", PLUGIN, f"--basetemp={base}").assert_outcomes(passed=3)


@not_built
def test_failed_policy_keeps_setup_and_teardown_failures(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # retention only applies without --basetemp, so give pytest its own temproot
    temproot = pytester.mkdir("temproot")
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(temproot))
    Root(temproot)  # fail here, not in the child, while the core is a stub
    pytester.makeini("[pytest]\ntmp_path_retention_policy = failed\n")
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture
        def broken_setup(tmp_path):
            raise RuntimeError("setup")

        @pytest.fixture
        def broken_teardown(tmp_path):
            yield
            raise RuntimeError("teardown")

        def test_passes(tmp_path): pass
        def test_setup(broken_setup): pass
        def test_teardown(broken_teardown): pass
        """
    )
    _run(pytester, "-p", PLUGIN)
    kept = {p.name for p in temproot.rglob("test_*") if p.is_dir()}
    assert any(name.startswith("test_setup") for name in kept)
    assert any(name.startswith("test_teardown") for name in kept)
    assert not any(name.startswith("test_passes") for name in kept)


@not_built
def test_xdist_workers_join_the_controllers_run(pytester: pytest.Pytester) -> None:
    pytest.importorskip("xdist")
    base = pytester.path / "base"
    Root(base)  # fail here, not in the child, while the core is a stub
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.parametrize("n", range(4))
        def test_item(tmp_path, n):
            print(tmp_path.parent)
        """
    )
    result = _run(pytester, "-p", PLUGIN, f"--basetemp={base}", "-n", "2", "-s")
    result.assert_outcomes(passed=4)
    runs = [p for p in base.iterdir() if p.is_dir()]
    assert len(runs) == 1
    assert len(list(runs[0].iterdir())) >= 4


def test_existing_basetemp_is_refused(pytester: pytest.Pytester) -> None:
    existing = pytester.mkdir("existing")
    precious = existing / "precious.txt"
    precious.write_text("keep me")
    pytester.makepyfile("def test_nothing(): pass")
    result = _run(pytester, "-p", PLUGIN, f"--basetemp={existing}")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--basetemp=*existing already exists*"])
    assert precious.read_text() == "keep me"


@not_built
def test_new_basetemp_is_created_and_never_pruned(pytester: pytest.Pytester) -> None:
    base = pytester.path / "new"
    Root(base)  # fail here, not in the child, while the core is a stub
    pytester.makeini(
        "[pytest]\ntmp_path_retention_policy = none\ntmp_path_retention_count = 0\n"
    )
    pytester.makepyfile("def test_passes(tmp_path): (tmp_path / 'f').touch()")
    _run(pytester, "-p", PLUGIN, f"--basetemp={base}").assert_outcomes(passed=1)
    assert list(base.rglob("f"))


@not_built
def test_xdist_workers_accept_the_new_basetemp(pytester: pytest.Pytester) -> None:
    pytest.importorskip("xdist")
    base = pytester.path / "new"
    Root(base)  # fail here, not in the child, while the core is a stub
    pytester.makepyfile("def test_one(tmp_path): pass\ndef test_two(tmp_path): pass")
    result = _run(pytester, "-p", PLUGIN, f"--basetemp={base}", "-n", "2")
    result.assert_outcomes(passed=2)


def test_fresh_empty_basetemp_is_used_with_a_warning(pytester: pytest.Pytester) -> None:
    fresh = pytester.mkdir("fresh")
    pytester.makepyfile("def test_nothing(): pass")
    result = _run(pytester, "-p", PLUGIN, f"--basetemp={fresh}")
    result.assert_outcomes(passed=1, warnings=1)
    result.stdout.fnmatch_lines(["*--basetemp=*fresh already exists; using it*"])


def test_old_empty_basetemp_is_refused(pytester: pytest.Pytester) -> None:
    old = pytester.mkdir("old")
    an_hour_ago = time.time() - 3600
    os.utime(old, (an_hour_ago, an_hour_ago))
    pytester.makepyfile("def test_nothing(): pass")
    result = _run(pytester, "-p", PLUGIN, f"--basetemp={old}")
    assert result.ret == pytest.ExitCode.USAGE_ERROR


def test_pytesters_own_subprocess_runs_work_with_a_warning(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile("def test_nothing(): pass")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", PLUGIN)
    result.assert_outcomes(passed=1, warnings=1)
