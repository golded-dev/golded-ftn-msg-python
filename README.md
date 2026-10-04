# golded-ftn-msg

Read and write classic FTSC-style `.MSG` areas with a 190-byte header.
Python 3.12 or newer. MIT licensed. Version 1.0.0.

The public API exports `MsgReader` and `MsgWriter`. Message values, options and
protocols come from `golded-ftn>=1.0.0,<2`.

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
  golded-ftn/
  golded-ftn-msg/
```

```sh
cd golded-ftn-msg
uv sync --locked
uv run pytest
```

`uv` uses the sibling core checkout during development. Wheel and sdist dependency
metadata contains the version constraint only. To install local built wheels:

```sh
uv build ../golded-ftn --out-dir /tmp/golded-wheels
uv build --out-dir /tmp/golded-wheels
uv pip install /tmp/golded-wheels/*.whl
```

## Behaviour and limits

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

This package does not interpret Opus headers, discover areas, handle other message
formats, provide a database, or integrate with Nornir.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the
[release checklist](docs/release.md).
