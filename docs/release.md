# Release checklist

The repository prepares version 1.0.0. Tags, remote setup and publication are
separate actions and require explicit authorization.

1. Review the scope and changelog. Confirm the version and core dependency range.
2. Run the checks in CONTRIBUTING.md on a clean checkout with the sibling core.
3. Inspect wheel/sdist metadata and contents with `scripts/verify_distribution.py`.
   It rebuilds from sdist and tests both MSG wheels with a local core wheel in
   separate clean environments outside the checkouts.
4. Make the core repository available to GitHub Actions. CI defaults to
   `golded-dev/golded-ftn`; set the `GOLDED_FTN_REPOSITORY` repository variable to
   the actual `owner/repository` when different. Its source must satisfy the lock.
   The checkout requires accessible hosting; local preparation does not confirm
   that hosting exists. Review CI results: Linux Python 3.12–3.14 runs checks and distribution testing;
   Windows and macOS Python 3.14 run tests. Record local platforms separately.
5. Confirm that local paths appear only in development configuration, never in
   distribution dependency metadata. Check README examples against installed code.
6. After explicit authorization, create the intended commit/tag and publish the
   reviewed archives. Check the resulting package page and installation.

Do not describe a prepared package as published until publication is confirmed.
