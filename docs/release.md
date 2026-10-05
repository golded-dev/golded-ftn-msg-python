# Release checklist

The repository prepares version 1.2.0. Tags, remote setup and publication are
separate actions and require explicit authorization.

1. Review the scope and changelog. Confirm the version and core dependency range.
2. Run the checks in CONTRIBUTING.md on a clean checkout with the sibling core.
3. Inspect wheel/sdist metadata and contents with `scripts/verify_distribution.py`.
   It rebuilds from sdist and tests both MSG wheels with a local core wheel in
   separate clean environments outside the checkouts.
4. Make the core repository available to GitHub Actions. CI defaults to
   `golded-dev/golded-ftn-python`; set the `GOLDED_FTN_REPOSITORY` repository variable to
   the actual `owner/repository` when different. Its source must satisfy the lock.
   The checkout requires accessible hosting; local preparation does not confirm
   that hosting exists. Review CI results: Linux Python 3.12–3.14 runs checks and distribution testing;
   Windows and macOS Python 3.14 run tests. Record local platforms separately.
5. Confirm that local paths appear only in development configuration, never in
   distribution dependency metadata. Check README examples against installed code.
6. After explicit authorization, create the intended commit/tag and publish the
   reviewed archives. Check the resulting package page and installation.

Do not describe a prepared package as published until publication is confirmed.

For writer changes, check create/read/append/update/delete through public sessions,
independent binary fixtures, stale revisions, controlled lock contention and
handled write/flush failures. Run examples against the installed wheel. Record
platform results separately: local macOS tests do not establish Linux or Windows
execution, or GoldED compatibility. Keep `concurrent=True` disabled until both
competing writes and GoldED read/cache/refresh checks pass against a pinned build.
The current GoldED build and integration tests are deferred.

## Local 1.2.0 release candidate — 2026-10-05

Verified on macOS 27.0 arm64 with CPython 3.14.6. The checkout contains
uncommitted changes; these checks cover the working tree, not a tagged release.

- `uv sync --locked`: passed.
- Ruff lint and format checks, strict mypy: passed.
- `uv run pytest -q`: 111 passed, 0 skipped.
- `uv build` and `uv run twine check dist/*`: passed for wheel and sdist.
- `scripts/verify_distribution.py`: passed metadata and package-content checks,
  byte comparison against a wheel rebuilt from sdist, isolated installed-package
  tests and strict consumer typing. Format packages also pass installed stubtest.
- `agent-compose check`: passed using the local mostly-agents tool.
- `git diff --check`: passed (whitespace only).

GitHub's API reports the repository as public and private vulnerability reporting
as enabled. PyPI's project JSON endpoint returned HTTP 404 on this date. No package
was uploaded. Local checks do not establish Linux/Windows or remote CI results.
GoldED build interoperability remains deferred; concurrent use stays disabled.

Release order: publish `golded-ftn==1.2.0` first, then the four format packages.
Each format package requires `golded-ftn>=1.2.0,<2`. Before publication, commit
and review CI for these exact sources, create the intended release tag, and
confirm the package-index destination and publishing authority. After core is
available, verify resolution from that index without local uv sources. Publish
only the reviewed archives, then check public installation and update the shared
guide's commit pins to the released commits. These remote actions are not part
of this local preparation.

Archive checksums are recorded separately in `RELEASE-SHA256.txt` at the
repository root, outside the archives, after the final build.
