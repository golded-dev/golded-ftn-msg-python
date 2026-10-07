"""Opus writes checked against literal header bytes and external records."""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from golded_ftn import (
    ConflictError,
    FtnAddress,
    MessagePatch,
    OutgoingMessage,
)

from golded_ftn_msg import MsgReader, MsgWriter


def outgoing(**changes: Any) -> OutgoingMessage:
    from dataclasses import replace

    return replace(
        OutgoingMessage(
            from_name="Alice", to_name="Bob", subject="Test", body_text="Hi"
        ),
        **changes,
    )


def test_opus_append_literal_header(tmp_path: Path) -> None:
    writer = MsgWriter()
    writer.create(tmp_path, header_format="opus")
    with writer.open(tmp_path, header_format="opus") as session:
        result = session.append(
            outgoing(
                posted_at=datetime(2024, 10, 5, 13, 24, 56),
                from_address=FtnAddress.from_string("2:230/150.3"),
                to_address=FtnAddress.from_string("3:231/1.4"),
                reply_to_msgno=7,
                reply1st_msgno=9,
            )
        )
        raw = (tmp_path / "1.MSG").read_bytes()
        assert raw[176:184] == bytes.fromhex("45 59 1c 6b 00 00 00 00")
        assert raw[184:190] == bytes.fromhex("07 00 00 00 09 00")
        assert b"\x01INTL: 3:231/1 2:230/150\r" in raw
        assert b"\x01FMPT: 3\r\x01TOPT: 4\r" in raw
        read = session.read(1)
        assert read.identity.format == "opus"
        assert read.revision == result.revision
        assert read.message.from_address == "2:230/150.3"
        assert read.message.to_address == "3:231/1.4"
        assert read.message.posted_at == datetime(2024, 10, 5, 13, 24, 56)


def test_external_opus_update_preserves_arrived_and_metadata(tmp_path: Path) -> None:
    raw = bytearray(190)
    raw[:6] = b"Alice\0"
    raw[36:40] = b"Bob\0"
    raw[72:77] = b"Test\0"
    raw[144:164] = b"05 Oct 24  13:24:56\0"
    raw[164:176] = bytes.fromhex("19 00 01 00 96 00 21 00 e6 00 e7 00")
    raw[176:184] = bytes.fromhex("45 59 1c 6b 46 59 00 60")
    raw[184:190] = bytes.fromhex("07 00 01 00 09 00")
    path = tmp_path / "7.MSG"
    path.write_bytes(bytes(raw) + b"\x01INTL: 3:231/1 2:230/150\r\x01FMPT: 3\rHi\0")
    with MsgWriter().open(tmp_path, header_format="opus") as session:
        original = session.read(7)
        changed = session.update(
            original.identity, MessagePatch(subject="New"), original.revision
        )
        assert (
            path.read_bytes()[144:]
            == bytes(raw)[144:] + b"\x01INTL: 3:231/1 2:230/150\r\x01FMPT: 3\rHi\0"
        )
        changed = session.update(
            changed.identity,
            MessagePatch(
                from_address=FtnAddress.from_string("4:240/160.5"),
                body_text="Changed",
            ),
            changed.revision,
        )
        assert path.read_bytes()[176:184] == bytes(raw)[176:184]
        assert path.read_bytes()[164:166] == bytes(raw)[164:166]
        assert path.read_bytes()[170:172] == bytes(raw)[170:172]
        assert session.read(7).message.from_address == "4:240/160.5"
        changed = session.update(
            changed.identity,
            MessagePatch(posted_at=datetime(2001, 2, 3, 4, 5, 6)),
            changed.revision,
        )
        assert path.read_bytes()[176:180] == bytes.fromhex("43 2a a3 20")
        assert path.read_bytes()[180:184] == bytes(raw)[180:184]
        with pytest.raises(ConflictError):
            session.update(
                original.identity, MessagePatch(subject="Stale"), original.revision
            )
        session.delete(changed.identity, changed.revision)
        assert not path.exists()
        assert session.append(outgoing()).identity.msgno == 8


@pytest.mark.parametrize(
    "date",
    [
        datetime(1979, 12, 31),
        datetime(2070, 1, 1),
        datetime(2108, 1, 1),
        datetime(2024, 1, 1, second=1),
        datetime(2024, 1, 1, microsecond=1),
    ],
)
def test_opus_rejects_unrepresentable_dates(tmp_path: Path, date: datetime) -> None:
    with MsgWriter().open(tmp_path, header_format="opus") as session:
        with pytest.raises(ValueError):
            session.append(outgoing(posted_at=date))
    assert not list(tmp_path.glob("*.MSG"))


@pytest.mark.parametrize(
    "date", [None, datetime(1980, 1, 1), datetime(2069, 12, 31, 23, 59, 58)]
)
def test_opus_date_boundaries(tmp_path: Path, date: datetime | None) -> None:
    with MsgWriter().open(tmp_path, header_format="opus") as session:
        result = session.append(outgoing(posted_at=date))
        assert session.read(result.identity.msgno).message.posted_at == date
    assert (tmp_path / "1.MSG").read_bytes()[180:184] == bytes(4)
    assert list(MsgReader("opus").read(tmp_path))[0].posted_at == date


def test_opus_rejects_conflicting_address_before_mutation(tmp_path: Path) -> None:
    with MsgWriter().open(tmp_path, header_format="opus") as session:
        with pytest.raises(ValueError):
            session.append(
                outgoing(
                    from_address=FtnAddress.from_string("2:230/150.3"),
                    body_text="\x01INTL: 3:231/1 4:230/150",
                )
            )
        with pytest.raises(ValueError):
            session.append(outgoing(from_address=FtnAddress.from_string("2:230/150")))
    assert not list(tmp_path.glob("*.MSG"))


@pytest.mark.parametrize("operation", ["append", "update", "delete"])
def test_opus_rollback_restores_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    with MsgWriter().open(tmp_path, header_format="opus") as session:
        initial = session.append(outgoing(posted_at=datetime(2024, 10, 5, 13, 24, 56)))
        path = tmp_path / "1.MSG"
        original = path.read_bytes()
        flush = session._flush_area
        calls = 0

        def fail_once() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("injected flush failure")
            flush()

        monkeypatch.setattr(session, "_flush_area", fail_once)
        with pytest.raises(OSError, match="injected"):
            if operation == "append":
                session.append(outgoing())
            elif operation == "update":
                session.update(
                    initial.identity, MessagePatch(subject="New"), initial.revision
                )
            else:
                session.delete(initial.identity, initial.revision)
        assert path.read_bytes() == original
        assert not (tmp_path / "2.MSG").exists()
        assert session.read(1).revision == initial.revision
        assert session.append(outgoing()).identity.msgno == 2
