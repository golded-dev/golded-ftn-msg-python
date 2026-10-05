# golded-ftn-msg

Repository: [`golded-ftn-msg-python`](https://github.com/golded-dev/golded-ftn-msg-python).
The distribution remains `golded-ftn-msg`; imports use `golded_ftn_msg`.
The source is public on GitHub. This package has not been released on PyPI.

Read FTSC and Opus `.MSG` areas with a 190-byte header; write classic FTSC headers.
Python 3.12 or newer. MIT licensed. Version 1.2.0 is prepared locally;
these writer changes are unreleased.

The public API exports `MsgReader`, `MsgWriter` and `MsgSession`. Message values, options and
protocols come from `golded-ftn>=1.2.0,<2`.

```python
from pathlib import Path

from golded_ftn import MessagePatch, OutgoingMessage
from golded_ftn_msg import MsgReader, MsgWriter

area = Path("messages")
writer = MsgWriter()
writer.create(area)
with writer.open(area) as session:
    added = session.append(
        OutgoingMessage(
            from_name="Alice", to_name="Bob", subject="Hello", body_text="First line"
        )
    )
    current = session.read(added.identity.msgno)
    session.update(current.identity, MessagePatch(subject="Revised"), current.revision)
assert list(MsgReader().read(area))[-1].subject == "Revised"
```

## Installation

For local development, keep the two repositories beside each other:

```text
golded-dev/
  golded-ftn-python/
  golded-ftn-msg-python/
```

```sh
cd golded-ftn-msg-python
uv sync --locked
uv run pytest
```

`uv` uses the sibling core checkout during development. Wheel and sdist dependency
metadata contains the version constraint only. To install local built wheels:

```sh
uv build ../golded-ftn-python --out-dir /tmp/golded-wheels
uv build --out-dir /tmp/golded-wheels
uv pip install /tmp/golded-wheels/*.whl
```

## Behaviour and limits

`MsgReader(header_format="ftsc")` is the default. Use `MsgReader("opus")`
explicitly for Opus areas; there is no automatic header detection. Opus bytes
176–183 contain DOS written/arrived timestamps rather than zone/point words.
The written timestamp supplies a naive date with two-second precision, using
1980–2107; zero or invalid written timestamps fall back to the textual date.
Addresses use header net/node plus INTL/FMPT/TOPT, without treating timestamp
bits as address metadata. Opus provenance uses `source_type="opus"`; FTSC
provenance remains `"msg"`. The writer continues to produce FTSC headers.

The reader accepts positive numeric filenames with case-insensitive `.msg`
extensions. It sorts numerically and rejects duplicate message numbers.
Filesystem errors identify missing or invalid area paths. Malformed message files
raise `ParserException` with the file path and original cause.

Text decoding is strict, using the core charset helpers and CP850 fallback.
Kludges remain in the normalized body; mojibake is never repaired automatically.
Dates use English month names and the 1970–2069 two-digit year window. Invalid
reader dates become `None`. Header addresses and INTL/FMPT/TOPT must agree.
Provenance records the actual file path and message number; offsets are unknown.

`MsgWriter.create(path)` initializes a new area. A context-managed `open(path)`
session provides `read`, `append`, `update` and `delete`. Updates and deletes
require the revision returned by a consistent session read. Unrelated message
changes do not invalidate that revision. Unknown header bytes and controls survive
updates; attribute-only updates preserve the original text bytes. The revision
contains format/base/message identity, the physical filename number and SHA-256
over the raw file. Omitted patch fields stay unchanged; explicit `None` clears
only representable optional values. `control_lines` replaces general controls;
omitted MSGID, addresses and routing retain their structured fields. Body-only
changes preserve controls and routing. Conflicting metadata is rejected.

Appends publish a complete temporary file without replacing an existing number.
Updates replace the complete file; deletes remove it. A sidecar lock serializes
Python sessions and retains the highest allocated number. Each operation rolls
back I/O failures where possible; a failed rollback poisons the session. The legacy
`write` convenience method returns the number of completed appends. Earlier
operations remain committed if a later one fails.

Only offline FTSC editing is supported. Opus editing and `concurrent=True` are
rejected. GoldED read/write interoperability is pending. POSIX publication uses a
hard link; Windows uses a non-replacing rename. Windows runtime behaviour has not
been verified. Linux execution has not been exercised here either. Process death
and power loss are outside the rollback guarantee;
a controlled exit test observes an unpublished temporary file after interrupted
append.

Header names allow 35 encoded bytes; subjects allow 71. Encoding is strict.
Header fields reject nulls and line breaks. Dates must be naive, have no
microseconds, and fall within 1970–2069. Address components and attributes must
fit unsigned 16-bit fields. Domain addresses are unsupported.

The writer preserves body kludges, quoting and routing, and adds missing declared
control lines, charset and address kludges. Conflicting metadata is rejected.
An external MSGID may be supplied; synthetic hash IDs cannot become MSGID.
MSGID is never generated automatically. Provenance is not serialized. Body lines
use CR and end with one null byte.

## Development

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python -m mypy.stubtest golded_ftn_msg
uv build
uv run twine check dist/*
uv run python scripts/verify_distribution.py
```

See [writer source notes](docs/writer-sources.md) for the original GoldED layout,
locking limits and the distinction between source evidence and build tests.

## Archive mode

Strict reading remains the default. For damaged archives, opt in explicitly:

```python
from golded_ftn import ReaderIssue, ReaderOptions
from golded_ftn_msg import MsgReader

issues: list[ReaderIssue] = []
options = ReaderOptions(archive_mode=True, on_issue=issues.append)
# Replace "messages" with the actual archive directory.
messages = list(MsgReader("opus").read("messages", options))
```

Archive mode requires a callback. It skips malformed message files with a
`record_parse_error` issue and continues to later files. If declared ASCII cannot
decode a message, it tries the configured fallback strictly and reports
`ascii_decode_fallback` after successful parsing. Invalid UTF-8 and address
conflicts are skipped, never repaired. Original charset controls remain unchanged.
Duplicate numeric filenames make identity ambiguous: the reader reports
`duplicate_message_number` with action `stopped` before reading any messages.
Callback exceptions propagate. `ReaderIssue` carries the source identity, action,
code and a description without message contents. Recovery includes the failed
ASCII byte offset; record errors without a known location use `None`.

This package does not discover areas, provide a database, or integrate with Nornir.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the
[release checklist](docs/release.md).
