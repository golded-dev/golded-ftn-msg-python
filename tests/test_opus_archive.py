"""Synthetic Opus DOS words and explicit archive recovery contracts."""

from datetime import datetime
from pathlib import Path

import pytest
from golded_ftn import ParserException, ReaderIssue, ReaderOptions

from golded_ftn_msg import MsgReader


def message(
    body: bytes = b"body\r\0",
    *,
    date: bytes = b"",
    written: tuple[int, int] = (0, 0),
    arrived: tuple[int, int] = (0, 0),
) -> bytes:
    raw = bytearray(190)
    for offset, value in ((0, b"From"), (36, b"To"), (72, b"Subject"), (144, date)):
        raw[offset : offset + len(value)] = value
    for offset, word in (
        (166, 1),
        (168, 150),
        (172, 230),
        (174, 231),
        (176, written[0]),
        (178, written[1]),
        (180, arrived[0]),
        (182, arrived[1]),
    ):
        raw[offset : offset + 2] = word.to_bytes(2, "little")
    return bytes(raw) + body


def test_opus_written_date_and_addresses(tmp_path: Path) -> None:
    written = (((2024 - 1980) << 9) | (10 << 5) | 5, (13 << 11) | (24 << 5) | 28)
    body = b"\x01INTL 2:231/1 2:230/150\r\x01FMPT 3\r\x01TOPT 4\rbody\r"
    file = tmp_path / "7.MSG"
    file.write_bytes(
        message(
            body, date=b"01 Jan 99  00:00:00", written=written, arrived=(65535, 65535)
        )
    )
    result = list(MsgReader(header_format="opus").read(tmp_path))[0]
    assert result.posted_at == datetime(2024, 10, 5, 13, 24, 56)
    assert (result.from_address, result.to_address) == ("2:230/150.3", "2:231/1.4")
    assert result.provenance is not None
    assert result.provenance.source_type == "opus"
    assert result.provenance.source_path == str(file)


@pytest.mark.parametrize("written", [(0, 0), (0xFFFF, 0xFFFF), (0x5820, 0)])
def test_opus_invalid_binary_date_uses_text(
    tmp_path: Path, written: tuple[int, int]
) -> None:
    (tmp_path / "1.MSG").write_bytes(
        message(date=b"01 Jan 24  12:34:56", written=written)
    )
    result = list(MsgReader("opus").read(tmp_path))[0]
    assert result.posted_at == datetime(2024, 1, 1, 12, 34, 56)
    assert result.from_address is result.to_address is None


def test_ftsc_remains_default(tmp_path: Path) -> None:
    (tmp_path / "1.MSG").write_bytes(message(written=(2, 2), arrived=(4, 3)))
    result = list(MsgReader().read(tmp_path))[0]
    assert result.from_address == "2:230/150.3"
    assert result.to_address == "2:231/1.4"
    assert result.provenance is not None and result.provenance.source_type == "msg"


def test_invalid_header_format() -> None:
    with pytest.raises(ValueError, match="header_format"):
        MsgReader("guess")  # type: ignore[arg-type]


def test_archive_skips_corrupt_file_and_continues(tmp_path: Path) -> None:
    (tmp_path / "1.MSG").write_bytes(b"short")
    (tmp_path / "2.MSG").write_bytes(message())
    issues: list[ReaderIssue] = []
    result = list(
        MsgReader().read(
            tmp_path, ReaderOptions(archive_mode=True, on_issue=issues.append)
        )
    )
    assert [m.msgno for m in result] == [2]
    assert len(issues) == 1
    assert issues[0].action == "skipped" and issues[0].code == "record_parse_error"
    assert issues[0].source_id == "1"
    assert issues[0].source_path == str(tmp_path / "1.MSG")
    assert issues[0].source_type == "msg"
    with pytest.raises(ParserException):
        list(MsgReader().read(tmp_path))


def test_archive_ascii_recovery_uses_strict_fallback(tmp_path: Path) -> None:
    (tmp_path / "1.MSG").write_bytes(
        message(b"\x01CHRS: ASCII 1\r" + "blå".encode("cp850"))
    )
    issues: list[ReaderIssue] = []
    options = ReaderOptions(archive_mode=True, on_issue=issues.append)
    result = list(MsgReader().read(tmp_path, options))
    assert result[0].body_text.endswith("blå")
    assert len(issues) == 1
    assert (issues[0].action, issues[0].code) == ("recovered", "ascii_decode_fallback")
    assert issues[0].source_offset == 207
    assert result[0].control_lines is not None
    assert result[0].control_lines.charset == "ASCII 1"
    with pytest.raises(ParserException):
        list(MsgReader().read(tmp_path))


def test_archive_fallback_still_strict_and_other_charsets_skip(tmp_path: Path) -> None:
    issues: list[ReaderIssue] = []
    options = ReaderOptions(
        fallback_charset="UTF-8", archive_mode=True, on_issue=issues.append
    )
    (tmp_path / "1.MSG").write_bytes(message(b"\x01CHRS: ASCII 1\r\xff"))
    (tmp_path / "2.MSG").write_bytes(message(b"\x01CHRS: UTF-8 4\r\xff"))
    (tmp_path / "3.MSG").write_bytes(message())
    assert [m.msgno for m in MsgReader().read(tmp_path, options)] == [3]
    assert [issue.action for issue in issues] == ["skipped", "skipped"]


def test_archive_ascii_header_recovery(tmp_path: Path) -> None:
    raw = bytearray(message(b"\x01CHRS: ASCII 1\rbody"))
    raw[0:2] = "ø".encode("cp850") + b"\0"
    (tmp_path / "1.MSG").write_bytes(raw)
    issues: list[ReaderIssue] = []
    result = list(
        MsgReader("opus").read(
            tmp_path, ReaderOptions(archive_mode=True, on_issue=issues.append)
        )
    )
    assert result[0].from_name == "ø"
    assert issues[0].source_offset == 0
    assert issues[0].source_type == "opus"


def test_archive_address_conflicts_skip(tmp_path: Path) -> None:
    (tmp_path / "1.MSG").write_bytes(message(b"\x01INTL 2:231/1 2:230/151\r"))
    issues: list[ReaderIssue] = []
    assert (
        list(
            MsgReader("opus").read(
                tmp_path, ReaderOptions(archive_mode=True, on_issue=issues.append)
            )
        )
        == []
    )
    assert len(issues) == 1 and issues[0].action == "skipped"


def test_archive_duplicate_number_is_reported(tmp_path: Path) -> None:
    for name in ("01.MSG", "1.MSG", "2.MSG"):
        (tmp_path / name).write_bytes(message())
    issues: list[ReaderIssue] = []
    result = list(
        MsgReader().read(
            tmp_path, ReaderOptions(archive_mode=True, on_issue=issues.append)
        )
    )
    assert result == []
    assert len(issues) == 1 and issues[0].code == "duplicate_message_number"
    assert issues[0].action == "stopped"
    assert issues[0].source_path == str(tmp_path / "1.MSG")


def test_callback_failure_is_not_swallowed(tmp_path: Path) -> None:
    (tmp_path / "1.MSG").write_bytes(b"short")

    def callback(issue: ReaderIssue) -> None:
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        list(
            MsgReader().read(
                tmp_path, ReaderOptions(archive_mode=True, on_issue=callback)
            )
        )


@pytest.mark.parametrize("year", [1980, 2107])
def test_opus_date_limits(tmp_path: Path, year: int) -> None:
    written = (((year - 1980) << 9) | (12 << 5) | 31, 0)
    (tmp_path / "1.MSG").write_bytes(message(written=written))
    assert list(MsgReader("opus").read(tmp_path))[0].posted_at == datetime(year, 12, 31)


def test_archive_ascii_fallback_alias(tmp_path: Path) -> None:
    (tmp_path / "1.MSG").write_bytes(message(b"\x01CHRS: ASCII 1\r\x9b"))
    issues: list[ReaderIssue] = []
    result = list(
        MsgReader().read(
            tmp_path,
            ReaderOptions(
                fallback_charset="IBMPC", archive_mode=True, on_issue=issues.append
            ),
        )
    )
    assert result[0].body_text.endswith("ø")
    assert len(issues) == 1


def test_failed_record_detail_contains_no_message_content(tmp_path: Path) -> None:
    (tmp_path / "1.MSG").write_bytes(
        message(b"\x01CHRS: UTF-8 4\rprivate-contents\xff")
    )
    issues: list[ReaderIssue] = []
    assert (
        list(
            MsgReader().read(
                tmp_path, ReaderOptions(archive_mode=True, on_issue=issues.append)
            )
        )
        == []
    )
    assert "private-contents" not in issues[0].detail


def test_recovery_callback_failure_is_not_a_parse_failure(tmp_path: Path) -> None:
    (tmp_path / "1.MSG").write_bytes(message(b"\x01CHRS: ASCII 1\r\xff"))

    def callback(issue: ReaderIssue) -> None:
        raise ValueError("callback failure")

    with pytest.raises(ValueError, match="callback failure"):
        list(
            MsgReader().read(
                tmp_path, ReaderOptions(archive_mode=True, on_issue=callback)
            )
        )


def test_archive_rejects_conflicting_ids(tmp_path: Path) -> None:
    (tmp_path / "1.MSG").write_bytes(message(b"\x01MSGID: one\r\x01MSGID: two\r"))
    issues: list[ReaderIssue] = []
    assert (
        list(
            MsgReader().read(
                tmp_path, ReaderOptions(archive_mode=True, on_issue=issues.append)
            )
        )
        == []
    )
    assert issues[0].action == "skipped"


def test_archive_rejects_conflicting_charsets(tmp_path: Path) -> None:
    (tmp_path / "1.MSG").write_bytes(message(b"\x01CHRS: CP850 2\r\x01CHRS: UTF-8 4\r"))
    issues: list[ReaderIssue] = []
    assert (
        list(
            MsgReader().read(
                tmp_path, ReaderOptions(archive_mode=True, on_issue=issues.append)
            )
        )
        == []
    )
    assert issues[0].action == "skipped"


def test_archive_does_not_swallow_filesystem_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "1.MSG").write_bytes(message())
    failure = PermissionError("source unreadable")

    def fail_read(path: Path) -> bytes:
        raise failure

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    with pytest.raises(PermissionError) as caught:
        list(
            MsgReader().read(
                tmp_path, ReaderOptions(archive_mode=True, on_issue=lambda i: None)
            )
        )
    assert caught.value is failure
