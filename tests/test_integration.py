"""Public protocol and format checks independent of reader/writer internals."""

from datetime import datetime
from pathlib import Path

from golded_ftn import (
    FtnAddress,
    MessageBaseReader,
    MessageWriter,
    OutgoingMessage,
    ReaderOptions,
    WriterOptions,
)

from golded_ftn_msg import MsgReader, MsgWriter


def test_public_contracts_and_roundtrip(tmp_path: Path) -> None:
    reader: MessageBaseReader = MsgReader()
    writer: MessageWriter = MsgWriter()
    source = OutgoingMessage(
        from_name="Søren",
        to_name="Recipient",
        subject="Æble",
        body_text="Hello\n> quoted\nSEEN-BY: 234/1\nPATH: 234/2\n",
        from_address=FtnAddress(zone=2, net=234, node=1, point=3),
        to_address=FtnAddress(zone=2, net=234, node=2),
        posted_at=datetime(2069, 12, 31, 23, 59, 59),
        external_id="2:234/1 abc123",
        attributes_raw=65535,
    )
    assert writer.write(tmp_path, [source], WriterOptions()) == 1
    messages = list(reader.read(tmp_path, ReaderOptions()))
    assert len(messages) == 1
    message = messages[0]
    assert message.from_name == source.from_name
    assert message.subject == source.subject
    assert message.posted_at == source.posted_at
    assert message.attributes_raw == 65535
    assert message.from_address == "2:234/1.3"
    assert message.to_address == "2:234/2"
    assert message.external_id == source.external_id
    assert message.body_text.endswith(source.body_text)
    assert message.control_lines is not None
    assert message.control_lines.seen_by == ("234/1",)
    assert message.control_lines.path == ("234/2",)


def test_literal_header_fixture(tmp_path: Path) -> None:
    # Literal 13 uint16 fields in GoldED FidoHdr order, not shared pack helpers.
    words = bytes.fromhex(
        "0100 0200 0300 0400 0500 0600 0700 0800 0900 0a00 0b00 0c00 0d00"
    )
    raw = (
        b"Sender\x00"
        + b"\x00" * 29
        + b"Recipient\x00"
        + b"\x00" * 26
        + b"Subject\x00"
        + b"\x00" * 64
        + b"01 Jan 70  00:00:00\x00"
        + words
        + b"Body\r> quote\r\x00"
    )
    assert len(raw[:190]) == 190
    (tmp_path / "42.MSG").write_bytes(raw)
    message = next(iter(MsgReader().read(tmp_path)))
    assert message.from_name == "Sender"
    assert message.to_name == "Recipient"
    assert message.subject == "Subject"
    assert message.posted_at == datetime(1970, 1, 1)
    assert message.from_address == "8:5/3.10"
    assert message.to_address == "7:6/2.9"
    assert message.reply_to_msgno == 11
    assert message.attributes_raw == 12
    assert message.reply1st_msgno == 13
    assert message.body_text == "Body\n> quote\n"


def test_conflicting_zero_point_kludges(tmp_path: Path) -> None:
    import pytest
    from golded_ftn import ParserException

    raw = b"\x00" * 190 + b"\x01FMPT 0\r\x01FMPT 3\rBody\x00"
    (tmp_path / "1.MSG").write_bytes(raw)
    with pytest.raises(ParserException, match="Conflicting"):
        list(MsgReader().read(tmp_path))
