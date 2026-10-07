# Changelog

## 1.3.0 — 2026-10-07

- Add explicit Opus create/read/append/update/delete sessions and Opus identities.
- Write DOS timestamps with strict even-second dates in 1980–2069; initialize
  arrived words to zero and preserve them on updates.
- Represent zones/points through INTL/FMPT/TOPT and reject incomplete or conflicting
  address metadata before mutation. Preserve existing FTSC behavior.
- Check literal timestamp headers, external metadata, stale revisions and rollback.


## 1.2.0 — 2026-10-05

- Close temporary files before error cleanup on Windows.

- Strip development-only uv sources from source distributions.

- Add offline FTSC create/read/append/update/delete sessions and revision conflicts.
- Preserve raw metadata, serialize Python sessions and roll back operation failures.
- Keep Opus editing and GoldED coexistence disabled.

- Preserve omitted routing during body edits and restore earlier operations correctly after handled I/O failures.

## 1.1.0 — Unreleased

- Add explicit Opus reading with DOS written timestamps and kludge addresses.
- Add opt-in archive mode with reported ASCII fallback, per-file skips and
  an explicit stop for ambiguous numeric filenames. Strict FTSC reading and
  writer behavior remain the defaults.

## 1.0.0

- Reader and writer for classic FTSC-style `.MSG` areas with 190-byte headers.
- Strict encoding, address and control-line reconciliation, and append-only writes.
- Typed public API using `golded-ftn` contracts.

This entry describes the prepared version. It does not establish publication.
