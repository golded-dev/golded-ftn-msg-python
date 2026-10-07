# golded-ftn-msg

This Python package reads FTSC and explicit Opus .MSG areas with a 190-byte
header; writers require explicit FTSC or Opus selection. Opus timestamp words
are not addresses. Preserve arrived timestamps; new Opus dates must be naive,
even-second precision, in 1980–2069.
Shared values and protocols belong to golded-ftn. Discovery and databases belong
to callers.

Preserve message text, control lines and routing. Decode and encode strictly;
mojibake repair is a caller decision. Keep absent metadata as None. Reconcile
header addresses with INTL/FMPT/TOPT and reject conflicts. Writer sessions use the core lock manager on a private numbering sidecar.
Publish complete temporary files atomically without clobbering existing names;
updates replace whole files offline. Preserve raw header metadata and text bytes
unless explicitly patched. Rollback failures poison the session. GoldED must
remain closed: its FidoArea lock and unlock methods are empty.
Completed operations survive a later failure.

Protect behavior with independent binary fixtures, including offsets, charset,
date pivot, metadata conflicts and file creation failures. Run pytest, Ruff lint
and format checks, strict mypy and scripts/verify_distribution.py. Distribution
metadata must contain only the public golded-ftn version constraint, with the
sibling checkout used solely through uv development configuration.

Edit this fragment or agent-compose.toml, then preview, build and check.
Commits, remotes, tags and publication require an explicit request.

Strict reading stays the default. Archive mode requires an issue callback and
reports every recovery, skipped record and unsafe traversal stop. Keep source
paths, identities and byte offsets in issues; keep message contents out. Callback
failures propagate. Protect both modes with independent synthetic fixtures.
