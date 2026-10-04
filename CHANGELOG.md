# Changelog

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
