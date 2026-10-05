"""Reader for classic 190-byte FTSC .MSG files."""

import codecs
import re
from collections.abc import Iterator
from os import PathLike
from pathlib import Path
from typing import Literal

from golded_ftn import (
    MessageProvenance,
    ParsedMessage,
    ParserException,
    ReaderIssue,
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
    parse_opus_date,
    read_words,
    resolve_addresses,
)


def _validate_archive_charsets(raw: bytes) -> None:
    identities: set[str] = set()
    for match in re.finditer(
        rb"\x01(?:CHRS|CHARSET):\s*([^\s\x00\x01]+)", raw, re.IGNORECASE
    ):
        declaration = match[0]
        cp = codecs.lookup(detect_charset(declaration, "CP850")).name
        utf = codecs.lookup(detect_charset(declaration, "UTF-8")).name
        identities.add(cp if cp == utf else "unknown:" + match[1].upper().hex())
    if len(identities) > 1:
        raise ValueError("Conflicting charset declarations")


class MsgReader:
    """Read numeric files in a classic MSG area in ascending message order."""

    def __init__(self, header_format: Literal["ftsc", "opus"] = "ftsc") -> None:
        if header_format not in {"ftsc", "opus"}:
            raise ValueError("header_format must be 'ftsc' or 'opus'")
        self._header_format = header_format

    def _report(self, options: ReaderOptions, issue: ReaderIssue) -> None:
        assert options.on_issue is not None
        options.on_issue(issue)

    @property
    def _source_type(self) -> str:
        return "opus" if self._header_format == "opus" else "msg"

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
                if options.archive_mode:
                    self._report(
                        options,
                        ReaderIssue(
                            source_type=self._source_type,
                            source_path=str(file),
                            source_id=str(msgno),
                            action="stopped",
                            code="duplicate_message_number",
                            detail="Ambiguous numeric filenames; area reading stopped.",
                        ),
                    )
                    return
                raise ParserException(
                    f"Duplicate message number {msgno}: {seen[msgno]} and {file}"
                )
            seen[msgno] = file
        for msgno, file in files:
            try:
                message, recovered = self._read_file(file, msgno, options)
            except ParserException as error:
                if not options.archive_mode:
                    raise
                cause = type(error.__cause__).__name__
                self._report(
                    options,
                    ReaderIssue(
                        source_type=self._source_type,
                        source_path=str(file),
                        source_id=str(msgno),
                        action="skipped",
                        code="record_parse_error",
                        detail=f"Message record could not be parsed ({cause}).",
                    ),
                )
                continue
            if recovered is not None:
                self._report(options, recovered)
            yield message

    def _read_file(
        self, file: Path, msgno: int, options: ReaderOptions, raw: bytes | None = None
    ) -> tuple[ParsedMessage, ReaderIssue | None]:
        recovered: ReaderIssue | None = None
        try:
            raw = file.read_bytes() if raw is None else raw
            if len(raw) < HEADER_SIZE:
                raise ValueError(f"Header is shorter than {HEADER_SIZE} bytes")
            body_raw = raw[HEADER_SIZE:]
            charset = detect_charset(body_raw, options.fallback_charset)
            if options.archive_mode:
                _validate_archive_charsets(body_raw)
            fields = {
                name: read_null_padded_field(raw, offset, length)
                for name, (offset, length) in TEXT_FIELDS.items()
            }
            decode_offset = 0
            try:
                decoded = []
                for name in ("from_name", "to_name", "subject"):
                    decode_offset = TEXT_FIELDS[name][0]
                    decoded.append(to_utf8(fields[name], charset))
                decode_offset = HEADER_SIZE
                body = parse_body(to_utf8(body_raw, charset))
            except UnicodeDecodeError as error:
                if not options.archive_mode or codecs.lookup(charset).name != "ascii":
                    raise
                fallback = detect_charset(b"", options.fallback_charset)
                decoded = [
                    to_utf8(fields[name], fallback)
                    for name in ("from_name", "to_name", "subject")
                ]
                body = parse_body(to_utf8(body_raw, fallback))
                recovered = ReaderIssue(
                    source_type=self._source_type,
                    source_path=str(file),
                    source_id=str(msgno),
                    source_offset=decode_offset + error.start,
                    action="recovered",
                    code="ascii_decode_fallback",
                    detail=(
                        "ASCII decoding failed; decoded strictly "
                        "with configured fallback."
                    ),
                )
            from_name, to_name, subject = decoded
            posted_at = parse_date(fields["date"])
            if self._header_format == "opus":
                posted_at = parse_opus_date(raw) or posted_at
            controls = parse_controls(body)
            if options.archive_mode:
                identifiers: dict[str, str] = {}
                for control in controls.kludges:
                    if control.name in {"MSGID", "REPLY"}:
                        if (
                            control.name in identifiers
                            and identifiers[control.name] != control.value
                        ):
                            raise ValueError(f"Conflicting {control.name} declarations")
                        identifiers[control.name] = control.value
            words = read_words(raw)
            if self._header_format == "opus":
                for side in ("from", "to"):
                    words[f"{side}_zone"] = words[f"{side}_point"] = 0
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
            message = ParsedMessage(
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
                    source_type=self._source_type,
                    source_path=str(file),
                    source_id=str(msgno),
                ),
            )
            return message, recovered
        except (OSError, ValueError, LookupError) as error:
            if options.archive_mode and isinstance(error, OSError):
                raise
            raise ParserException(f"Cannot parse MSG file {file}: {error}") from error
