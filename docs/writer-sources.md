# MSG writer source notes

Reference checkout: `golded-open-source`, commit
`600266252b73174ff5116cee697ef9a97aeb1859`. Paths below are relative to that repo.

- `goldlib/gmb3/gmofido.h:74–99`: the packed 190-byte header, FTSC zone/point
  fields, Opus timestamp union, reply-to and first-reply words.
- `goldlib/gmb3/gmofido4.cpp:43–58`: `FidoArea::lock` and `unlock` are empty.
  A private Python sidecar cannot coordinate GoldED access.
- `goldlib/gmb3/gmofido4.cpp:63–237`: filename allocation, deletion, header and
  text serialization. The original writer may truncate an existing message;
  Python publishes a complete replacement file in offline mode.
- `goldlib/gmb3/gmofido2.cpp:38–75`: scanning discovers numeric `.msg` names and
  builds a cached sorted message index. External publication does not by itself
  establish safe refresh in a running GoldED.

Python writes FTSC only. Explicit Opus reading remains supported, but Opus
mutation is rejected because its timestamp words are not FTSC addresses.
`create` initializes an empty area. Opening a session bootstraps the private
`.golded-ftn-msg.lock` numbering sidecar. Existing directory contents are rejected. Existing lastread files are
untouched during editing. Each operation uses the shared sidecar descriptor and
monotonic lock timeout; unrelated messages do not invalidate a revision.

The default encoding is CP850. Serialization is strict. Omitted patch fields
retain their values; explicit `None` clears representable optional metadata.
MSGID, addresses and routing remain separate from general controls. A body patch
retains omitted controls and routing. Attribute-only changes retain text bytes;
all changes start with the raw header rather than rebuilding hidden fields from
the parsed model.

Appends publish a complete temporary file without replacing an existing filename
(POSIX hard link, Windows non-replacing rename). Updates replace the whole file;
deletes remove it. The sidecar keeps allocated numbers after deletion. Failed
operations restore the affected file and sidecar where possible; rollback failure
makes the session unusable. Earlier completed operations survive later failures.
There is no process-kill or power-loss transaction guarantee.

Tests use independent FTSC/Opus headers, raw metadata and control fixtures,
public CRUD operations, stale revisions, same-process sessions and controlled
helper processes. Injected write/flush failures check the rollback boundary.
One forced-exit probe leaves a private temporary file while the original indexed
message survives. It does not establish crash recovery for every write stage.

Only macOS runtime tests have been run in this checkout. Linux and Windows
execution are unverified. GoldED build, read/write interoperability and refresh
checks are deferred. `concurrent=True` is rejected on every platform; keep GoldED
closed and avoid direct base-file access during session operations.
