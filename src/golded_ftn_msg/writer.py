"""Offline editing sessions for explicit FTSC and Opus messages."""

from __future__ import annotations

import codecs
import os
import re
import struct
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from os import PathLike
from pathlib import Path
from typing import Literal, Self, cast

from golded_ftn import (
    UNSET,
    ConflictError,
    ControlLine,
    FtnAddress,
    MessageIdentity,
    MessagePatch,
    OutgoingMessage,
    ParserException,
    ReaderOptions,
    RevisionToken,
    RollbackError,
    SessionMessage,
    UnsupportedOperationError,
    WriterError,
    WriteResult,
    WriterOptions,
    detect_charset,
)
from golded_ftn._writer_io import IO, locks, raw_revision, strict_encode

from ._format import (
    HEADER_SIZE,
    TEXT_FIELDS,
    WORD_OFFSETS,
    format_date,
    parse_controls,
    resolve_addresses,
    uint16,
)
from .reader import MsgReader, _validate_archive_charsets


def _charset(value: str) -> str:
    token = value.split()[0] if value.split() else ""
    detected = detect_charset(f"\x01CHRS: {token}".encode("ascii"), "utf-32")
    if detected == "utf-32":
        raise ValueError(f"Unknown charset declaration: {value}")
    return codecs.lookup(detected).name


def _control(name: str, value: str) -> ControlLine:
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", name) is None:
        raise ValueError(f"Invalid control name: {name!r}")
    if any(char in value for char in "\r\n\x00\x01"):
        raise ValueError("Control values must be single lines without nulls")
    return ControlLine(name=name.upper(), value=value, raw="")


def _validate_metadata(raw: bytes, charset: str) -> None:
    _validate_archive_charsets(raw[HEADER_SIZE:])
    controls = parse_controls(
        raw[HEADER_SIZE:].decode(
            detect_charset(raw[HEADER_SIZE:], charset), errors="strict"
        )
    )
    for name in ("MSGID", "REPLY"):
        values = {control.value for control in controls.kludges if control.name == name}
        if len(values) > 1:
            raise ParserException(f"Conflicting {name} metadata")


def _validate_addresses(message: OutgoingMessage, controls: list[ControlLine]) -> None:
    declarations: dict[str, object] = {}
    for control in controls:
        name = control.name.upper()
        if name == "INTL":
            tokens = control.value.split()
            if len(tokens) != 2:
                raise ValueError("Invalid INTL declaration")
            addresses = tuple(FtnAddress.try_from_string(token) for token in tokens)
            if any(
                a is None or a.domain is not None or a.point is not None
                for a in addresses
            ):
                raise ValueError("Invalid INTL declaration")
            for declared, explicit in zip(
                addresses, (message.to_address, message.from_address), strict=True
            ):
                assert declared is not None
                for component in (declared.zone, declared.net, declared.node):
                    uint16(component)
                if explicit is not None and (
                    declared.zone,
                    declared.net,
                    declared.node,
                ) != (explicit.zone, explicit.net, explicit.node):
                    raise ValueError("Conflicting INTL address metadata")
            value: object = addresses
        elif name in ("FMPT", "TOPT"):
            if re.fullmatch(r"[0-9]+", control.value) is None:
                raise ValueError(f"Invalid {name} declaration")
            point = uint16(int(control.value))
            explicit = message.from_address if name == "FMPT" else message.to_address
            if explicit is not None and point != (explicit.point or 0):
                raise ValueError(f"Conflicting {name} address metadata")
            value = point
        else:
            continue
        if name in declarations and declarations[name] != value:
            raise ValueError(f"Conflicting {name} declarations")
        declarations[name] = value


def _opus_timestamp(value: datetime | None) -> bytes:
    if value is None:
        return bytes(4)
    if (
        value.tzinfo is not None
        or value.microsecond
        or value.second % 2
        or not 1980 <= value.year <= 2069
    ):
        raise ValueError("Opus date must be naive, even-second precision, in 1980–2069")
    date = ((value.year - 1980) << 9) | (value.month << 5) | value.day
    time = (value.hour << 11) | (value.minute << 5) | (value.second // 2)
    return struct.pack("<HH", date, time)


def _serialize(
    message: OutgoingMessage,
    charset: str,
    header_format: Literal["ftsc", "opus"] = "ftsc",
) -> bytes:
    header = bytearray(HEADER_SIZE)
    for name in ("from_name", "to_name", "subject"):
        value = getattr(message, name)
        if any(char in value for char in "\r\n\x00\x01"):
            raise ValueError(f"Invalid {name}: null or newline")
        encoded = value.encode(charset, errors="strict")
        offset, size = TEXT_FIELDS[name]
        if len(encoded) >= size:
            raise ValueError(f"{name} exceeds {size - 1} encoded bytes")
        header[offset : offset + len(encoded)] = encoded
    timestamp = _opus_timestamp(message.posted_at) if header_format == "opus" else None
    date = format_date(message.posted_at)
    header[144 : 144 + len(date)] = date
    words = dict.fromkeys(WORD_OFFSETS, 0)
    words["attributes"] = uint16(message.attributes_raw or 0)
    words["reply_to"] = uint16(message.reply_to_msgno or 0)
    words["first_reply"] = uint16(message.reply1st_msgno or 0)
    if message.reply_next_msgno or message.reply_list:
        raise ValueError("MSG represents only reply_to and first_reply links")
    for side, address in (("from", message.from_address), ("to", message.to_address)):
        if address is None:
            continue
        if address.domain is not None:
            raise ValueError("Domain addresses cannot be represented in MSG headers")
        for component in ("zone", "net", "node", "point"):
            words[f"{side}_{component}"] = uint16(getattr(address, component) or 0)
    if "\x00" in message.body_text:
        raise ValueError("Body cannot contain embedded nulls")
    body = message.body_text.replace("\r\n", "\n").replace("\r", "\n")
    for label, values in (
        ("SEEN-BY", message.routing_seen_by),
        ("PATH", message.routing_path),
    ):
        for value in values:
            if any(char in value for char in "\r\n\x00\x01"):
                raise ValueError("Routing values must be single lines")
            line = f"{label}: {value}" if label == "SEEN-BY" else f"\x01PATH: {value}"
            if line not in body.split("\n"):
                body += ("\n" if body else "") + line
    controls = list(parse_controls(body).kludges)
    added: list[ControlLine] = []

    def add(name: str, value: str) -> None:
        control = _control(name, value)
        if not any(
            c.name.upper() == control.name and c.value == value for c in controls
        ):
            controls.append(control)
            added.append(control)

    for control in message.control_lines:
        add(control.name, control.value)
    ids = {c.value for c in controls if c.name.upper() == "MSGID"}
    external = message.external_id
    if external is not None:
        _control("MSGID", external)
        if external.startswith("hash:sha256:"):
            raise ValueError("Synthetic IDs cannot be serialized as MSGID")
        if ids and ids != {external}:
            raise ValueError("Conflicting MSGID metadata")
        add("MSGID", external)
    if len(ids) > 1 or any(value.startswith("hash:sha256:") for value in ids):
        raise ValueError("Conflicting or synthetic MSGID metadata")
    declared = [c for c in controls if c.name.upper() in ("CHRS", "CHARSET")]
    canonical = codecs.lookup(charset).name
    if any(_charset(c.value) != canonical for c in declared):
        raise ValueError("Charset declaration conflicts with target encoding")
    if not declared:
        label = canonical.upper().replace("_", "-")
        if _charset(label) != canonical:
            raise ValueError("Target charset cannot be declared")
        level = 4 if canonical == "utf-8" else 1 if canonical == "ascii" else 2
        add("CHRS", f"{label} {level}")
    _validate_addresses(message, controls)
    if header_format == "opus":
        for side in ("from", "to"):
            words[f"{side}_zone"] = words[f"{side}_point"] = 0
    resolve_addresses(words, controls)
    if message.from_address is not None and message.to_address is not None:
        if not any(c.name.upper() == "INTL" for c in controls):
            origin, destination = message.from_address, message.to_address
            add(
                "INTL",
                f"{destination.zone}:{destination.net}/{destination.node} "
                f"{origin.zone}:{origin.net}/{origin.node}",
            )
    for name, address in (("FMPT", message.from_address), ("TOPT", message.to_address)):
        if (
            address is not None
            and address.point
            and not any(c.name.upper() == name for c in controls)
        ):
            add(name, str(address.point))
    resolved_origin, resolved_destination = resolve_addresses(words, controls)
    if header_format == "opus":
        for explicit, represented in (
            (message.from_address, resolved_origin),
            (message.to_address, resolved_destination),
        ):
            if explicit is not None and (
                represented is None
                or (explicit.zone, explicit.net, explicit.node, explicit.point or 0)
                != (
                    represented.zone,
                    represented.net,
                    represented.node,
                    represented.point or 0,
                )
            ):
                raise ValueError(
                    "Opus zones require INTL with both origin and destination"
                )
    for name, offset in WORD_OFFSETS.items():
        struct.pack_into("<H", header, offset, words[name])
    if timestamp is not None:
        header[176:180] = timestamp
        header[180:184] = bytes(4)
    prefix = "".join(f"\x01{c.name}: {c.value}\n" for c in added)
    raw = (
        bytes(header)
        + (prefix + body).replace("\n", "\r").encode(charset, errors="strict")
        + b"\x00"
    )
    _validate_metadata(raw, charset)
    return raw


class MsgWriter:
    """Explicit FTSC/Opus mutation sessions. GoldED must remain closed."""

    def create(
        self,
        path: str | PathLike[str],
        *,
        header_format: Literal["ftsc", "opus"] = "ftsc",
    ) -> None:
        if header_format not in {"ftsc", "opus"}:
            raise ValueError("header_format must be 'ftsc' or 'opus'")
        area = Path(path)
        if area.exists() and any(area.iterdir()):
            raise FileExistsError(str(area))
        area.mkdir(parents=True, exist_ok=True)

    def open(
        self,
        path: str | PathLike[str],
        options: WriterOptions | None = None,
        *,
        header_format: Literal["ftsc", "opus"] = "ftsc",
    ) -> MsgSession:
        if header_format not in {"ftsc", "opus"}:
            raise ValueError("header_format must be 'ftsc' or 'opus'")
        return MsgSession(
            Path(path).resolve(), options or WriterOptions(), header_format
        )

    def write(
        self,
        path: str | PathLike[str],
        messages: Iterable[OutgoingMessage],
        options: WriterOptions | None = None,
        *,
        header_format: Literal["ftsc", "opus"] = "ftsc",
    ) -> int:
        # The batch convenience method uses the same per-message commit boundary.
        area = Path(path)
        area.mkdir(parents=True, exist_ok=True)
        count = 0
        for message in messages:
            _serialize(
                message, (options or WriterOptions()).target_charset, header_format
            )
            with self.open(area, options, header_format=header_format) as session:
                session.append(message)
            count += 1
        return count


class MsgSession:
    def __init__(
        self,
        base: Path,
        options: WriterOptions,
        header_format: Literal["ftsc", "opus"] = "ftsc",
    ) -> None:
        if header_format not in {"ftsc", "opus"}:
            raise ValueError("header_format must be 'ftsc' or 'opus'")
        self.header_format = header_format
        if options.concurrent:
            raise UnsupportedOperationError("MSG does not share a GoldED lock")
        if not base.is_dir():
            raise FileNotFoundError(str(base))
        self.base, self.options = base, options
        self._closed = self._poisoned = False
        self._io = IO()
        self._lock_path = base / ".golded-ftn-msg.lock"
        if self._lock_path.is_symlink():
            raise ParserException("Unsafe MSG numbering sidecar")
        # Serialize creation/close with the manager's descriptor acquisition.
        locks.ensure_file(self._lock_path)

    def __enter__(self) -> Self:
        self._check()
        return self

    def __exit__(self, *args: object) -> None:
        self._closed = True

    def _check(self) -> None:
        if self._closed or self._poisoned:
            raise WriterError("MSG session is closed or unusable")

    @contextmanager
    def _operation(self) -> Iterator[tuple[int, dict[int, tuple[Path, bytes]]]]:
        self._check()
        with locks.acquire(self._lock_path, timeout=self.options.lock_timeout) as fd:
            records: dict[int, tuple[Path, bytes]] = {}
            for entry in self.base.iterdir():
                if (
                    entry.suffix.lower() != ".msg"
                    or re.fullmatch(r"[0-9]+", entry.stem) is None
                ):
                    continue
                number = int(entry.stem)
                if number < 1:
                    continue
                if number in records or entry.is_symlink() or not entry.is_file():
                    raise ParserException("Ambiguous or unsafe MSG filename")
                raw = entry.read_bytes()
                _validate_metadata(raw, self.options.target_charset)
                MsgReader(self.header_format)._read_file(
                    entry,
                    number,
                    ReaderOptions(fallback_charset=self.options.target_charset),
                    raw,
                )
                records[number] = entry, raw
            yield fd, records

    def _identity(self, number: int) -> MessageIdentity:
        return MessageIdentity(
            format="opus" if self.header_format == "opus" else "msg",
            base=str(self.base),
            msgno=number,
        )

    def _revision(self, number: int, raw: bytes) -> RevisionToken:
        return raw_revision(self._identity(number), (number,), raw)

    def _target(
        self,
        records: dict[int, tuple[Path, bytes]],
        identity: MessageIdentity,
        expected: RevisionToken,
    ) -> tuple[Path, bytes]:
        if identity != self._identity(identity.msgno) or identity.msgno not in records:
            raise ConflictError("MSG target is missing or belongs to another area")
        path, raw = records[identity.msgno]
        if self._revision(identity.msgno, raw) != expected:
            raise ConflictError("MSG target revision changed")
        return path, raw

    def read(self, msgno: int) -> SessionMessage:
        with self._operation() as (_fd, records):
            if msgno not in records:
                raise ConflictError("MSG target is missing")
            path, raw = records[msgno]
            message, _issue = MsgReader(self.header_format)._read_file(
                path,
                msgno,
                ReaderOptions(fallback_charset=self.options.target_charset),
                raw,
            )
            return SessionMessage(
                message=message,
                identity=self._identity(msgno),
                revision=self._revision(msgno, raw),
            )

    def _temp(self, data: bytes) -> Path:
        fd, name = tempfile.mkstemp(prefix=".golded-ftn-msg-", dir=self.base)
        path = Path(name)
        try:
            try:
                self._io.write(fd, 0, data)
                self._io.flush(fd)
            finally:
                os.close(fd)
        except BaseException:
            path.unlink()
            raise
        return path

    def _flush_area(self) -> None:
        if os.name != "nt":
            fd = os.open(self.base, os.O_RDONLY)
            try:
                self._io.flush(fd)
            finally:
                os.close(fd)

    def _publish(self, temporary: Path, target: Path) -> None:
        if os.name == "nt":
            os.rename(temporary, target)  # Windows rename refuses an existing target.
        else:
            os.link(temporary, target)  # Atomic complete-file publication, no clobber.

    def append(self, message: OutgoingMessage) -> WriteResult:
        data = _serialize(message, self.options.target_charset, self.header_format)
        with self._operation() as (fd, records):
            saved = self._io.read(fd, 0, os.fstat(fd).st_size)
            if saved and len(saved) != 8:
                raise ParserException("Invalid MSG numbering sidecar")
            high = int.from_bytes(saved, "little")
            number = max(high, max(records, default=0)) + 1
            if number > 0xFFFFFFFF:
                raise ValueError("MSG file number exceeds unsigned 32-bit range")
            target = self.base / f"{number}.MSG"
            temporary = self._temp(data)
            published = False
            try:
                self._publish(temporary, target)
                published = True
                self._io.write(fd, 0, number.to_bytes(8, "little"))
                self._io.flush(fd)
                self._flush_area()
            except BaseException as original:
                try:
                    if published:
                        target.unlink()
                    self._io.write(fd, 0, saved)
                    self._io.truncate(fd, len(saved))
                    self._io.flush(fd)
                    self._flush_area()
                except BaseException as failure:
                    self._poisoned = True
                    raise RollbackError(
                        f"{self.base}: append failed ({original}); "
                        f"rollback failed ({failure})"
                    ) from failure
                raise
            finally:
                temporary.unlink(missing_ok=True)
            return WriteResult(
                identity=self._identity(number), revision=self._revision(number, data)
            )

    def _replace(
        self, target: Path, data: bytes, original: bytes, operation: str
    ) -> None:
        temporary = self._temp(data)
        replaced = False
        try:
            os.replace(temporary, target)
            replaced = True
            self._flush_area()
        except BaseException as error:
            if replaced:
                try:
                    restore = self._temp(original)
                    try:
                        os.replace(restore, target)
                        self._flush_area()
                    finally:
                        restore.unlink(missing_ok=True)
                except BaseException as failure:
                    self._poisoned = True
                    raise RollbackError(
                        f"{self.base}: {operation} failed ({error}); "
                        f"rollback failed ({failure})"
                    ) from failure
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def delete(
        self, identity: MessageIdentity, expected_revision: RevisionToken
    ) -> MessageIdentity:
        with self._operation() as (fd, records):
            path, original = self._target(records, identity, expected_revision)
            saved = self._io.read(fd, 0, os.fstat(fd).st_size)
            if saved and len(saved) != 8:
                raise ParserException("Invalid MSG numbering sidecar")
            high = max(int.from_bytes(saved, "little"), max(records, default=0))
            removed = False
            try:
                self._io.write(fd, 0, high.to_bytes(8, "little"))
                self._io.flush(fd)
                path.unlink()
                removed = True
                self._flush_area()
            except BaseException as error:
                try:
                    self._io.write(fd, 0, saved)
                    self._io.truncate(fd, len(saved))
                    self._io.flush(fd)
                except BaseException as failure:
                    self._poisoned = True
                    raise RollbackError(
                        f"{self.base}: delete failed ({error}); "
                        f"rollback failed ({failure})"
                    ) from failure
                if removed:
                    try:
                        restore = self._temp(original)
                        try:
                            self._publish(restore, path)
                            self._flush_area()
                        finally:
                            restore.unlink(missing_ok=True)
                    except BaseException as failure:
                        self._poisoned = True
                        raise RollbackError(
                            f"{self.base}: delete failed ({error}); "
                            f"rollback failed ({failure})"
                        ) from failure
                raise
            return identity

    def update(
        self,
        identity: MessageIdentity,
        patch: MessagePatch,
        expected_revision: RevisionToken,
    ) -> WriteResult:
        if (
            patch.provenance is not UNSET
            or patch.reply_next_msgno is not UNSET
            or patch.reply_list is not UNSET
        ):
            raise ValueError(
                "MSG cannot patch provenance or additional reply structures"
            )
        with self._operation() as (_fd, records):
            path, original = self._target(records, identity, expected_revision)
            parsed, _issue = MsgReader(self.header_format)._read_file(
                path,
                identity.msgno,
                ReaderOptions(fallback_charset=self.options.target_charset),
                original,
            )
            body = parsed.body_text
            controls = parsed.control_lines
            assert controls is not None
            header = bytearray(original[:190])
            text_changed = any(
                getattr(patch, name) is not UNSET
                for name in (
                    "body_text",
                    "control_lines",
                    "external_id",
                    "routing_seen_by",
                    "routing_path",
                    "from_address",
                    "to_address",
                )
            )
            header_encoded = any(
                getattr(patch, name) is not UNSET
                for name in ("from_name", "to_name", "subject")
            )
            if text_changed or header_encoded:
                strict_encode("", self.options, controls.kludges)
            for name in ("from_name", "to_name", "subject"):
                value = getattr(patch, name)
                if value is UNSET:
                    continue
                if value is None or any(char in value for char in "\r\n\x00\x01"):
                    raise ValueError(f"Invalid {name}")
                encoded = value.encode(self.options.target_charset, errors="strict")
                offset, size = TEXT_FIELDS[name]
                if len(encoded) >= size:
                    raise ValueError(f"{name} exceeds {size - 1} encoded bytes")
                header[offset : offset + size] = encoded + bytes(size - len(encoded))
            if patch.posted_at is not UNSET:
                value = cast(datetime | None, patch.posted_at)
                if self.header_format == "opus":
                    header[176:180] = _opus_timestamp(value)
                date = format_date(value)
                header[144:164] = date + bytes(20 - len(date))
            for name, word in (
                ("attributes_raw", "attributes"),
                ("reply_to_msgno", "reply_to"),
                ("reply1st_msgno", "first_reply"),
            ):
                value = getattr(patch, name)
                if value is not UNSET:
                    if name == "attributes_raw" and value is None:
                        raise ValueError("attributes_raw cannot be cleared with None")
                    struct.pack_into(
                        "<H", header, WORD_OFFSETS[word], uint16(value or 0)
                    )
            if text_changed:
                if patch.body_text is not UNSET:
                    if patch.body_text is None:
                        raise ValueError("body_text cannot be cleared with None")
                    body = cast(str, patch.body_text)
                    if patch.control_lines is UNSET:
                        # Keep original control spelling and whitespace on body edits.
                        prefix = "\n".join(
                            line
                            for line in parsed.body_text.splitlines()
                            if line.startswith("\x01")
                        )
                        body = prefix + "\n" + body if prefix else body
                inherited = list(controls.kludges)
                if patch.control_lines is not UNSET:
                    inherited = list(
                        cast(tuple[ControlLine, ...] | None, patch.control_lines) or ()
                    )
                    body = "\n".join(
                        line
                        for line in body.splitlines()
                        if not line.startswith("\x01")
                    )
                external = (
                    None
                    if parsed.external_id is None
                    or parsed.external_id.startswith("hash:sha256:")
                    else parsed.external_id
                )
                if patch.external_id is not UNSET:
                    external = cast(str | None, patch.external_id)
                    inherited = [c for c in inherited if c.name != "MSGID"]
                    body = "\n".join(
                        line
                        for line in body.splitlines()
                        if not re.match(r"\x01MSGID(?:[: ]|$)", line, re.IGNORECASE)
                    )
                from_address = (
                    FtnAddress.from_string(parsed.from_address)
                    if parsed.from_address
                    else None
                )
                to_address = (
                    FtnAddress.from_string(parsed.to_address)
                    if parsed.to_address
                    else None
                )
                if patch.from_address is not UNSET:
                    from_address = cast(FtnAddress | None, patch.from_address)
                if patch.to_address is not UNSET:
                    to_address = cast(FtnAddress | None, patch.to_address)
                if patch.from_address is not UNSET or patch.to_address is not UNSET:
                    inherited = [
                        c for c in inherited if c.name not in {"INTL", "FMPT", "TOPT"}
                    ]
                    body = "\n".join(
                        line
                        for line in body.splitlines()
                        if not re.match(
                            r"\x01(?:INTL|FMPT|TOPT)(?:[: ]|$)", line, re.IGNORECASE
                        )
                    )
                seen_by = controls.seen_by
                route = controls.path
                if patch.routing_seen_by is not UNSET:
                    seen_by = cast(tuple[str, ...] | None, patch.routing_seen_by) or ()
                    body = "\n".join(
                        line
                        for line in body.splitlines()
                        if not line.startswith("SEEN-BY:")
                    )
                if patch.routing_path is not UNSET:
                    route = cast(tuple[str, ...] | None, patch.routing_path) or ()
                    inherited = [c for c in inherited if c.name != "PATH"]
                    body = "\n".join(
                        line
                        for line in body.splitlines()
                        if not re.match(r"\x01PATH(?:[: ]|$)", line, re.IGNORECASE)
                    )
                outgoing = OutgoingMessage(
                    from_name="",
                    to_name="",
                    subject="",
                    body_text=body,
                    external_id=external,
                    from_address=from_address,
                    to_address=to_address,
                    control_lines=tuple(inherited),
                    routing_seen_by=seen_by,
                    routing_path=route,
                )
                rebuilt = _serialize(
                    outgoing, self.options.target_charset, self.header_format
                )
                for side, value in (
                    ("from", patch.from_address),
                    ("to", patch.to_address),
                ):
                    if value is UNSET:
                        continue
                    parts = (
                        ("net", "node")
                        if self.header_format == "opus"
                        else ("zone", "net", "node", "point")
                    )
                    for part in parts:
                        offset = WORD_OFFSETS[f"{side}_{part}"]
                        header[offset : offset + 2] = rebuilt[offset : offset + 2]
                data = bytes(header) + rebuilt[190:]
            else:
                data = bytes(header) + original[190:]
            _validate_metadata(data, self.options.target_charset)
            MsgReader(self.header_format)._read_file(
                path,
                identity.msgno,
                ReaderOptions(fallback_charset=self.options.target_charset),
                data,
            )
            self._replace(path, data, original, "update")
            return WriteResult(
                identity=identity, revision=self._revision(identity.msgno, data)
            )
