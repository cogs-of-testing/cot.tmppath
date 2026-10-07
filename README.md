# cot.tmppath

A hardened, fast building block for related temporary folders: one folder
per run, items side by side inside it, kept or removed by policy, safe with
concurrent processes. For test runners (a pytest binding) and for
cot.runsomewhere's bootstrap staging and worker scratch.

Early. The goals are in [docs/goals.md](docs/goals.md), the background in
[docs/research.md](docs/research.md), and `testing/` states the goals as
tests. How the core keeps them is in [docs/core.md](docs/core.md).

```bash
uv run pytest -q
uv run mypy
uv run --group bench pytest benchmarks
```

pytest projects can opt in to having pytest's `tmp_path` and
`tmp_path_factory` replaced. The `py.path` fixtures `tmpdir` and
`tmpdir_factory` are not provided:

```ini
[pytest]
addopts = -p cot.tmppath.overtake_pytest
```

How and why is in [docs/pytest-replacement.md](docs/pytest-replacement.md).

Old runs can be removed by hand; it lists them and asks before removing:

```bash
python -m cot.tmppath prune --all-projects --older-than 7d
```
