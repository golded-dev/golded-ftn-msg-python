# Contributing

Use Python 3.12+ and uv. Keep `golded-ftn` beside this checkout, then run:

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python -m mypy.stubtest golded_ftn_msg
uv run pytest
uv build
uv run twine check dist/*
uv run python scripts/verify_distribution.py
```

Use synthetic fixtures. Do not commit private message archives, credentials or
access material. Keep format changes backed by independent binary fixtures;
round-trip tests alone can hide a shared reader/writer mistake.

Describe the behaviour changed and the checks run. Keep unrelated changes out.

Test FTSC and explicit Opus headers independently. Archive mode must report every
recovery, skip or stop through the required callback, while strict mode retains
its existing behavior. Use synthetic malformed files and callback failures in
regressions; avoid exposing message text through issue descriptions.
