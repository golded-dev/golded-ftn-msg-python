# golded-ftn-msg

This Python package reads and writes classic FTSC-style .MSG areas with a
190-byte little-endian header. Shared values and protocols belong to golded-ftn.
Opus headers, area discovery, databases and other formats are outside this package.

Preserve message text, control lines and routing. Decode and encode strictly;
mojibake repair is a caller decision. Keep absent metadata as None. Reconcile
header addresses with INTL/FMPT/TOPT and reject conflicts. Writers append through
exclusive creation; failure cleanup removes only the current operation's file.
Completed messages survive a later batch failure.

Protect behavior with independent binary fixtures, including offsets, charset,
date pivot, metadata conflicts and file creation failures. Run pytest, Ruff lint
and format checks, strict mypy and scripts/verify_distribution.py. Distribution
metadata must contain only the public golded-ftn version constraint, with the
sibling checkout used solely through uv development configuration.

Edit this fragment or agent-compose.toml, then preview, build and check.
Commits, remotes, tags and publication require an explicit request.
