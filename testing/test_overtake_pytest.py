"""cot.tmppath.overtake_pytest: opt-in takeover of pytest's tmp fixtures.

The mechanism tests pass already: the plugin creates its run lazily, so
loading it and handing out factories never touches the stubbed core. The
behaviour tests are xfail until the core is built.
"""

from __future__ import annotations

import pytest

from conftest import not_built
from cot.tmppath import Root

pytest_plugins = ["pytester"]

PLUGIN = "cot.tmppath.overtake_pytest"


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)


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
    pytester: pytest.Pytester,
) -> None:
    base = pytester.path / "base"
    Root(base)  # fail here, not in the child, while the core is a stub
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
    _run(pytester, "-p", PLUGIN, f"--basetemp={base}")
    kept = {p.name for run in base.iterdir() if run.is_dir() for p in run.iterdir()}
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
