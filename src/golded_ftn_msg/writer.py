"""Strict, append-only serialization of classic FTSC messages."""

import codecs
import re
import struct
from collections.abc import Iterable
from os import PathLike
from pathlib import Path

from golded_ftn import (
    ControlLine,
    FtnAddress,
    OutgoingMessage,
    WriterOptions,
    detect_charset,
)

from ._format import (
    HEADER_SIZE,
    TEXT_FIELDS,
    WORD_OFFSETS,
    format_date,
    parse_controls,
    resolve_addresses,
    uint16,
)


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


def _serialize(message: OutgoingMessage, charset: str) -> bytes:
    header = bytearray(HEADER_SIZE)
    for name in ("from_name", "to_name", "subject"):
        value = getattr(message, name)
        if any(char in value for char in "\r\n\x00"):
            raise ValueError(f"Invalid {name}: null or newline")
        encoded = value.encode(charset, errors="strict")
        offset, size = TEXT_FIELDS[name]
        if len(encoded) >= size:
            raise ValueError(f"{name} exceeds {size - 1} encoded bytes")
        header[offset : offset + len(encoded)] = encoded
    date = format_date(message.posted_at)
    header[144 : 144 + len(date)] = date
    words = dict.fromkeys(WORD_OFFSETS, 0)
    words["attributes"] = uint16(message.attributes_raw or 0)
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
    resolve_addresses(words, controls)
    for name, offset in WORD_OFFSETS.items():
        struct.pack_into("<H", header, offset, words[name])
    prefix = "".join(f"\x01{c.name}: {c.value}\n" for c in added)
    return (
        bytes(header)
        + (prefix + body).replace("\n", "\r").encode(charset, errors="strict")
        + b"\x00"
    )


class MsgWriter:
    """Append messages; completed earlier files survive a later batch failure."""

    def write(
        self,
        path: str | PathLike[str],
        messages: Iterable[OutgoingMessage],
        options: WriterOptions | None = None,
    ) -> int:
        area = Path(path)
        area.mkdir(parents=True, exist_ok=True)
        charset = (options or WriterOptions()).target_charset
        codecs.lookup(charset)
        number = max(
            (
                int(p.stem)
                for p in area.iterdir()
                if p.is_file()
                and p.suffix.lower() == ".msg"
                and p.stem.isascii()
                and p.stem.isdecimal()
                and int(p.stem) > 0
            ),
            default=0,
        )
        count = 0
        for message in messages:
            data = _serialize(message, charset)
            while True:
                number += 1
                target = area / f"{number}.MSG"
                try:
                    handle = target.open("xb")
                except FileExistsError:
                    continue
                try:
                    with handle:
                        if handle.write(data) != len(data):
                            raise OSError("Incomplete MSG file write")
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
                count += 1
                break
        return count
