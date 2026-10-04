# golded-ftn-msg

Repository: [`golded-ftn-msg-python`](https://github.com/golded-dev/golded-ftn-msg-python).
The distribution remains `golded-ftn-msg`; imports use `golded_ftn_msg`.
The source is public on GitHub. This package has not been released on PyPI.

Read FTSC and Opus `.MSG` areas with a 190-byte header; write classic FTSC headers.
Python 3.12 or newer. MIT licensed. Version 1.1.0.

The public API exports `MsgReader` and `MsgWriter`. Message values, options and
protocols come from `golded-ftn>=1.1.0,<2`.

```python
from pathlib import Path

from golded_ftn import OutgoingMessage, WriterOptions
from golded_ftn_msg import MsgReader, MsgWriter

area = Path("messages")
count = MsgWriter().write(
    area,
    [
        OutgoingMessage(
            from_name="Alice",
            to_name="Bob",
            subject="Hello",
            body_text="First line\nSecond line",
        )
    ],
    WriterOptions(target_charset="CP850"),
)
assert count == 1
messages = list(MsgReader().read(area))
assert messages[-1].subject == "Hello"
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

The writer appends numbered `.MSG` files using exclusive creation. It never
replaces an existing file. Messages are validated before creation; a failed write
removes only its current file. Earlier messages remain, so batches are not atomic.
Successful calls return the number of completed files.

Header names allow 35 encoded bytes; subjects allow 71. Encoding is strict.
Header fields reject nulls and line breaks. Dates must be naive, have no
microseconds, and fall within 1970–2069. Address components and attributes must
fit unsigned 16-bit fields. Domain addresses are unsupported.

The writer preserves body kludges, quoting and routing, and adds missing declared
control lines, charset and address kludges. Conflicting metadata is rejected.
An external MSGID may be supplied; synthetic hash IDs cannot become MSGID.
MSGID is never generated automatically. Provenance is not serialized. Body lines
use CR and end with one null byte.

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
