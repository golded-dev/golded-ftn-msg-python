"""Reader for classic 190-byte FTSC .MSG files."""

import re
from collections.abc import Iterator
from os import PathLike
from pathlib import Path

from golded_ftn import (
    MessageProvenance,
    ParsedMessage,
    ParserException,
    ReaderOptions,
    detect_charset,
    extract_msgid,
    parse_body,
    read_null_padded_field,
    synthetic_id,
    to_utf8,
)

from ._format import (
    HEADER_SIZE,
    TEXT_FIELDS,
    parse_controls,
    parse_date,
    read_words,
    resolve_addresses,
)


class MsgReader:
    """Read numeric files in a classic MSG area in ascending message order."""

    def read(
        self, path: str | PathLike[str], options: ReaderOptions | None = None
    ) -> Iterator[ParsedMessage]:
        options = options or ReaderOptions()
        files: list[tuple[int, Path]] = []
        for entry in Path(path).iterdir():
            if (
                entry.suffix.lower() == ".msg"
                and re.fullmatch(r"[0-9]+", entry.stem)
                and int(entry.stem) > 0
                and not entry.is_symlink()
                and entry.is_file()
            ):
                files.append((int(entry.stem), entry))
        files.sort(key=lambda item: (item[0], item[1].name))
        seen: dict[int, Path] = {}
        for msgno, file in files:
            if msgno in seen:
                raise ParserException(
                    f"Duplicate message number {msgno}: {seen[msgno]} and {file}"
                )
            seen[msgno] = file
        for msgno, file in files:
            yield self._read_file(file, msgno, options)

    @staticmethod
    def _read_file(file: Path, msgno: int, options: ReaderOptions) -> ParsedMessage:
        try:
            raw = file.read_bytes()
            if len(raw) < HEADER_SIZE:
                raise ValueError(f"Header is shorter than {HEADER_SIZE} bytes")
            body_raw = raw[HEADER_SIZE:]
            charset = detect_charset(body_raw, options.fallback_charset)
            fields = {
                name: read_null_padded_field(raw, offset, length)
                for name, (offset, length) in TEXT_FIELDS.items()
            }
            from_name = to_utf8(fields["from_name"], charset)
            to_name = to_utf8(fields["to_name"], charset)
            subject = to_utf8(fields["subject"], charset)
            body = parse_body(to_utf8(body_raw, charset))
            posted_at = parse_date(fields["date"])
            controls = parse_controls(body)
            words = read_words(raw)
            from_address, to_address = resolve_addresses(words, controls.kludges)
            external_id = extract_msgid(body)
            if external_id is None:
                external_id = synthetic_id(
                    from_name,
                    to_name,
                    subject,
                    posted_at.isoformat() if posted_at is not None else None,
                    body,
                )
            return ParsedMessage(
                msgno=msgno,
                from_name=from_name,
                to_name=to_name,
                subject=subject,
                body_text=body,
                attributes_raw=words["attributes"],
                posted_at=posted_at,
                external_id=external_id,
                from_address=str(from_address) if from_address is not None else None,
                to_address=str(to_address) if to_address is not None else None,
                reply_to_msgno=words["reply_to"] or None,
                reply1st_msgno=words["first_reply"] or None,
                control_lines=controls,
                provenance=MessageProvenance(
                    source_type="msg", source_path=str(file), source_id=str(msgno)
                ),
            )
        except (OSError, ValueError, LookupError) as error:
            raise ParserException(f"Cannot parse MSG file {file}: {error}") from error
