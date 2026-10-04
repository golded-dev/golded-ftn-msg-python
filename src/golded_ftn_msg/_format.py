"""Classic FTSC .MSG layout and shared metadata rules."""

import re
import struct
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from golded_ftn import ControlLine, FtnAddress, MessageControlLines, parse_message

HEADER_SIZE = 190
TEXT_FIELDS = {
    "from_name": (0, 36),
    "to_name": (36, 36),
    "subject": (72, 72),
    "date": (144, 20),
}
WORD_OFFSETS = {
    "times_read": 164,
    "to_node": 166,
    "from_node": 168,
    "cost": 170,
    "from_net": 172,
    "to_net": 174,
    "to_zone": 176,
    "from_zone": 178,
    "to_point": 180,
    "from_point": 182,
    "reply_to": 184,
    "attributes": 186,
    "first_reply": 188,
}
MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def read_words(raw: bytes) -> dict[str, int]:
    return {
        name: struct.unpack_from("<H", raw, offset)[0]
        for name, offset in WORD_OFFSETS.items()
    }


def uint16(value: int) -> int:
    if not 0 <= value <= 65535:
        raise ValueError(f"Value outside unsigned 16-bit range: {value}")
    return value


def parse_date(raw: bytes) -> datetime | None:
    try:
        match = re.fullmatch(
            r"(\d{1,2}) ([A-Za-z]{3}) (\d{2})\s+(\d{2}):(\d{2}):(\d{2})",
            raw.decode("ascii").strip(),
        )
        if match is None:
            return None
        day, month, year, hour, minute, second = match.groups()
        month_number = next(
            i for i, name in enumerate(MONTHS, 1) if name.lower() == month.lower()
        )
        y = int(year)
        return datetime(
            2000 + y if y <= 69 else 1900 + y,
            month_number,
            int(day),
            int(hour),
            int(minute),
            int(second),
        )
    except (ValueError, UnicodeError, StopIteration):
        return None


def parse_opus_date(raw: bytes) -> datetime | None:
    """Decode the written DOS date/time words at offsets 176 and 178."""
    date, time = struct.unpack_from("<HH", raw, 176)
    if date == time == 0:
        return None
    try:
        return datetime(
            1980 + (date >> 9),
            (date >> 5) & 15,
            date & 31,
            time >> 11,
            (time >> 5) & 63,
            (time & 31) * 2,
        )
    except ValueError:
        return None


def format_date(value: datetime | None) -> bytes:
    if value is None:
        return b""
    if value.tzinfo is not None or value.microsecond or not 1970 <= value.year <= 2069:
        raise ValueError("Date must be naive, whole-second precision, in 1970–2069")
    return (
        f"{value.day:02d} {MONTHS[value.month - 1]} {value.year % 100:02d}  "
        f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}"
    ).encode("ascii")


def resolve_addresses(
    words: dict[str, int], controls: Sequence[ControlLine]
) -> tuple[FtnAddress | None, FtnAddress | None]:
    parts: dict[str, list[int | None]] = {}
    for side in ("from", "to"):
        zone, net, node, point = (
            words[f"{side}_{key}"] for key in ("zone", "net", "node", "point")
        )
        # A nonzero net establishes the header node, including coordinator node 0.
        # Zero zone/net/point words remain absent for legacy kludge supplementation.
        parts[side] = [
            zone or None,
            net or None,
            node if node or net else None,
            point or None,
        ]

    declared: dict[tuple[str, int], int] = {}

    def merge(side: str, values: Sequence[int], *, point_only: bool = False) -> None:
        for index, value in enumerate(values):
            if point_only and index != 3:
                continue
            key = (side, index)
            if key in declared and declared[key] != value:
                raise ValueError(f"Conflicting {side} address kludges")
            declared[key] = value
            uint16(value)
            old = parts[side][index]
            if old is not None and old != value:
                raise ValueError(f"Conflicting {side} address metadata")
            parts[side][index] = value

    for control in controls:
        if control.name.upper() == "INTL":
            tokens = control.value.split()
            if len(tokens) != 2:
                continue
            addresses = [FtnAddress.try_from_string(token) for token in tokens]
            if any(
                address is None
                or address.domain is not None
                or address.point is not None
                for address in addresses
            ):
                continue
            for side, address in zip(("to", "from"), addresses, strict=True):
                assert address is not None
                merge(side, (address.zone, address.net, address.node))
        elif control.name.upper() in ("FMPT", "TOPT") and re.fullmatch(
            r"[0-9]+", control.value
        ):
            side = "from" if control.name.upper() == "FMPT" else "to"
            merge(side, (0, 0, 0, int(control.value)), point_only=True)
    result: list[FtnAddress | None] = []
    for side in ("from", "to"):
        resolved_zone, resolved_net, resolved_node, resolved_point = parts[side]
        result.append(
            FtnAddress(
                zone=resolved_zone,
                net=resolved_net,
                node=resolved_node,
                point=resolved_point or None,
            )
            if resolved_zone is not None
            and resolved_net is not None
            and resolved_node is not None
            else None
        )
    return result[0], result[1]


def parse_controls(text: str) -> MessageControlLines:
    """Include traditional space-separated address kludges beside core syntax."""
    parsed = parse_message(text)
    controls: list[ControlLine] = []
    existing = iter(parsed.kludges)
    for raw in re.split(r"\r\n|\r|\n", text):
        line = raw.rstrip("\x00")
        if re.fullmatch(r"\x01([A-Za-z][A-Za-z0-9-]*):\s*(.*)", line):
            controls.append(next(existing))
        else:
            match = re.fullmatch(r"\x01(INTL|FMPT|TOPT)\s+(.+)", line, re.IGNORECASE)
            if match:
                controls.append(
                    ControlLine(name=match[1].upper(), value=match[2].strip(), raw=raw)
                )
    return replace(parsed, kludges=tuple(controls))
