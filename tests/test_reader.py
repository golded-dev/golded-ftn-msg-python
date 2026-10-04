from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from golded_ftn import (
    MessageBaseReader,
    ParsedMessage,
    ParserException,
    ReaderOptions,
    synthetic_id,
)

from golded_ftn_msg.reader import MsgReader

FIXTURE = Path(__file__).parent / "fixtures" / "reader_classic.MSG"


def binary(
    body: bytes = b"body\r\0",
    date: bytes = b"",
    numeric: dict[str, int] | None = None,
    **words: int,
) -> bytes:
    header = bytearray(190)
    for offset, value in ((0, b"From"), (36, b"To"), (72, b"Subject"), (144, date)):
        header[offset : offset + len(value)] = value
    offsets = {
        "from_zone": 178,
        "from_net": 172,
        "from_node": 168,
        "to_zone": 176,
        "to_net": 174,
        "to_node": 166,
        "from_point": 182,
        "to_point": 180,
    }
    for key, number in (numeric or words).items():
        header[offsets[key] : offsets[key] + 2] = number.to_bytes(2, "little")
    return bytes(header) + body


def read_one(tmp_path: Path, raw: bytes) -> ParsedMessage:
    (tmp_path / "1.MSG").write_bytes(raw)
    return list(MsgReader().read(tmp_path))[0]


def test_independent_fixture(tmp_path: Path) -> None:
    (tmp_path / "12.MSG").write_bytes(FIXTURE.read_bytes())
    reader: MessageBaseReader = MsgReader()
    message = list(reader.read(tmp_path))[0]
    assert (message.from_name, message.to_name, message.subject) == (
        "Odinn Sørensen",
        "Gregory ThroatWobbler",
        "Keep on the good work..",
    )
    assert message.posted_at == datetime(2024, 1, 1, 12, 34, 56)
    assert (message.attributes_raw, message.reply_to_msgno, message.reply1st_msgno) == (
        257,
        9,
        11,
    )
    assert (message.from_address, message.to_address) == ("2:230/150.3", "2:231/151.4")
    assert message.external_id == "2:230/150.3 abc"
    assert message.body_text.endswith("Hello ø\nSEEN-BY: 230/150\n")
    assert message.control_lines is not None
    assert message.control_lines.seen_by == ("230/150",)
    assert message.provenance is not None
    assert message.provenance.source_path == str(tmp_path / "12.MSG")
    assert message.provenance.source_id == "12"
    assert message.provenance.source_offset is None
    assert message.reply_next_msgno is None


def test_discovery(tmp_path: Path) -> None:
    for name in (
        "10.MSG",
        "2.msg",
        "1.mSg",
        "0.MSG",
        "-1.MSG",
        "foo.MSG",
        "3x.MSG",
        "4.txt",
    ):
        (tmp_path / name).write_bytes(binary())
    (tmp_path / "8.MSG").mkdir()
    (tmp_path / "9.MSG").symlink_to(tmp_path / "1.mSg")
    assert [m.msgno for m in MsgReader().read(tmp_path)] == [1, 2, 10]


def test_duplicate_number(tmp_path: Path) -> None:
    for name in ("1.MSG", "01.msg"):
        (tmp_path / name).write_bytes(binary())
    with pytest.raises(ParserException, match="Duplicate message number 1"):
        list(MsgReader().read(tmp_path))


def test_filesystem_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list(MsgReader().read(tmp_path / "absent"))
    file = tmp_path / "file"
    file.touch()
    with pytest.raises(NotADirectoryError):
        list(MsgReader().read(file))


@pytest.mark.parametrize("raw", [b"short", binary(b"\x01CHRS: UTF-8 4\r\xff")])
def test_corrupt_file(tmp_path: Path, raw: bytes) -> None:
    (tmp_path / "1.MSG").write_bytes(raw)
    with pytest.raises(ParserException, match="1.MSG") as caught:
        list(MsgReader().read(tmp_path))
    assert caught.value.__cause__ is not None


def test_unreadable_file(tmp_path: Path) -> None:
    (tmp_path / "1.MSG").write_bytes(binary())
    with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
        with pytest.raises(ParserException) as caught:
            list(MsgReader().read(tmp_path))
    assert isinstance(caught.value.__cause__, PermissionError)


@pytest.mark.parametrize(
    "date,expected",
    [
        (b"01 Jan 00  00:00:00", datetime(2000, 1, 1)),
        (b"31 Dec 69  23:59:59", datetime(2069, 12, 31, 23, 59, 59)),
        (b"01 Jan 70  00:00:00", datetime(1970, 1, 1)),
        (b"01 Jan 99  00:00:00", datetime(1999, 1, 1)),
        (b"31 Feb 24  12:00:00", None),
        (b"01 Xxx 24  12:00:00", None),
        (b"", None),
    ],
)
def test_dates(tmp_path: Path, date: bytes, expected: datetime | None) -> None:
    assert read_one(tmp_path, binary(date=date)).posted_at == expected


def test_synthetic_id_and_fallback(tmp_path: Path) -> None:
    (tmp_path / "1.MSG").write_bytes(binary("ø\r\ntext\r\0\0".encode("latin1")))
    message = list(
        MsgReader().read(tmp_path, ReaderOptions(fallback_charset="latin1"))
    )[0]
    assert message.body_text == "ø\ntext\n"
    assert message.external_id == synthetic_id(
        "From", "To", "Subject", None, "ø\ntext\n"
    )
    assert message.reply_to_msgno is None and message.reply1st_msgno is None


@pytest.mark.parametrize("separator", [":", ""])
def test_address_kludges(tmp_path: Path, separator: str) -> None:
    body = (
        f"\x01INTL{separator} 2:231/151 2:230/150\r"
        f"\x01FMPT{separator} 3\r\x01TOPT{separator} 4\r"
    ).encode()
    message = read_one(tmp_path, binary(body, from_net=230, from_node=150))
    assert (message.from_address, message.to_address) == ("2:230/150.3", "2:231/151.4")
    assert message.control_lines is not None
    assert len(message.control_lines.kludges) == 3


@pytest.mark.parametrize(
    "body,words",
    [
        (b"\x01INTL: 2:231/151 3:230/150\r", {"from_zone": 2}),
        (b"\x01FMPT: 4\r", {"from_point": 3}),
        (b"\x01INTL: 2:231/151 2:230/150\r\x01INTL: 2:231/151 3:230/150\r", {}),
    ],
)
def test_address_conflicts(tmp_path: Path, body: bytes, words: dict[str, int]) -> None:
    with pytest.raises(ParserException, match="address"):
        read_one(tmp_path, binary(body, numeric=words))


def test_incomplete_and_invalid_addresses(tmp_path: Path) -> None:
    message = read_one(
        tmp_path,
        binary(
            b"\x01INTL: malformed\r\x01FMPT: not-a-point\r", from_net=230, from_node=150
        ),
    )
    assert message.from_address is None and message.to_address is None


def test_utf8_and_no_mojibake_repair(tmp_path: Path) -> None:
    message = read_one(tmp_path, binary("\x01CHRS: UTF-8 4\rÃ¸\r".encode()))
    assert message.body_text.endswith("Ã¸\n")


def test_empty_area(tmp_path: Path) -> None:
    assert list(MsgReader().read(tmp_path)) == []


def test_invalid_utf8_header(tmp_path: Path) -> None:
    raw = bytearray(binary(b"\x01CHRS: UTF-8 4\rbody\r"))
    raw[0] = 255
    with pytest.raises(ParserException) as caught:
        read_one(tmp_path, bytes(raw))
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)


def test_invalid_date_bytes_are_unknown(tmp_path: Path) -> None:
    message = read_one(tmp_path, binary(date=b"\xff"))
    assert message.posted_at is None


@pytest.mark.parametrize("with_intl", [False, True])
def test_zero_node_is_an_address(tmp_path: Path, with_intl: bool) -> None:
    body = b"\x01INTL 2:230/1 2:230/0\rbody\x00" if with_intl else b"body\x00"
    message = read_one(tmp_path, binary(body, from_zone=2, from_net=230, from_node=0))
    assert message.from_address == "2:230/0"


@pytest.mark.parametrize(
    "body,words",
    [
        (b"\x01FMPT 0\r", {"from_point": 5}),
        (b"\x01TOPT: 0\r", {"to_point": 5}),
        (b"\x01INTL 2:230/1 2:230/0\r", {"from_node": 150}),
        (b"\x01INTL 2:230/1 2:230/150\r", {"from_net": 230, "from_node": 0}),
    ],
)
def test_explicit_zero_address_conflicts(
    tmp_path: Path, body: bytes, words: dict[str, int]
) -> None:
    with pytest.raises(ParserException, match="address"):
        read_one(tmp_path, binary(body, numeric=words))


def test_intl_supplies_zero_node_without_header_address(tmp_path: Path) -> None:
    message = read_one(tmp_path, binary(b"\x01INTL 2:230/0 2:230/0\r"))
    assert message.from_address == message.to_address == "2:230/0"
