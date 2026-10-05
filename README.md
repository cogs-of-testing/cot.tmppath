# cot.tmppath

A hardened, fast building block for related temporary folders: one folder
per run, items side by side inside it, kept or removed by policy, safe with
concurrent processes. For test runners (a pytest binding) and for
cot.runsomewhere's bootstrap staging and worker scratch.

Not built yet. The goals are in [docs/goals.md](docs/goals.md) and the
background in [docs/research.md](docs/research.md). `src/cot/tmppath` holds
the intended API as stubs that raise `NotImplementedError`, and `testing/`
states the goals as tests, marked xfail until each behaviour lands.

```bash
uv run pytest -q
uv run mypy
uv run --group bench pytest benchmarks
```

There is no pytest binding. How pytest runs with its tmp fixtures replaced
is in [docs/pytest-replacement.md](docs/pytest-replacement.md).
