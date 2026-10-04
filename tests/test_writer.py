"""Writer checks inspect the independent wire layout, not reader round trips."""

import struct
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, cast

import pytest
from golded_ftn import ControlLine, FtnAddress, OutgoingMessage, WriterOptions

from golded_ftn_msg.writer import MsgWriter


def message(**changes: Any) -> OutgoingMessage:
    base = OutgoingMessage(
        from_name="Æ",
        to_name="Receiver",
        subject="Subject",
        body_text="Hi\n> quoted\r\nSEEN-BY: 1/2",
    )
    return replace(base, **changes)


def test_header_and_body_bytes(tmp_path: Path) -> None:
    msg = message(
        posted_at=datetime(2001, 2, 3, 4, 5, 6),
        attributes_raw=65535,
        from_address=FtnAddress(zone=2, net=3, node=4, point=5),
        to_address=FtnAddress(zone=6, net=7, node=8, point=9),
    )
    assert MsgWriter().write(tmp_path, [msg]) == 1
    raw = (tmp_path / "1.MSG").read_bytes()
    assert raw[:36] == "Æ".encode("cp850") + bytes(35)
    assert raw[36:72] == b"Receiver" + bytes(28)
    assert raw[72:144] == b"Subject" + bytes(65)
    assert raw[144:164] == b"03 Feb 01  04:05:06\x00"
    assert struct.unpack("<13H", raw[164:190]) == (
        0,
        8,
        4,
        0,
        3,
        7,
        6,
        2,
        9,
        5,
        0,
        65535,
        0,
    )
    assert (
        raw[190:] == b"\x01CHRS: CP850 2\r\x01INTL: 6:7/8 2:3/4\r"
        b"\x01FMPT: 5\r\x01TOPT: 9\rHi\r> quoted\rSEEN-BY: 1/2\x00"
    )


@pytest.mark.parametrize(
    "field,limit", [("from_name", 35), ("to_name", 35), ("subject", 71)]
)
def test_encoded_boundaries(tmp_path: Path, field: str, limit: int) -> None:
    MsgWriter().write(tmp_path, [message(**{field: "x" * limit})])
    with pytest.raises(ValueError):
        MsgWriter().write(tmp_path, [message(**{field: "x" * (limit + 1)})])
    assert len(list(tmp_path.iterdir())) == 1


def test_utf8_byte_limit_and_strict_encoding(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        MsgWriter().write(
            tmp_path,
            [message(from_name="Æ" * 18)],
            WriterOptions(target_charset="UTF-8"),
        )
    with pytest.raises(UnicodeEncodeError):
        MsgWriter().write(tmp_path, [message(body_text="🦄")])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"subject": "bad\x00"},
        {"to_name": "bad\r"},
        {"body_text": "bad\x00"},
        {"attributes_raw": -1},
        {"attributes_raw": 65536},
        {"posted_at": datetime(1969, 1, 1)},
        {"posted_at": datetime(2070, 1, 1)},
        {"posted_at": datetime(2000, 1, 1, microsecond=1)},
        {"posted_at": datetime(2000, 1, 1, tzinfo=UTC)},
        {"from_address": FtnAddress(zone=65536, net=1, node=2)},
        {"from_address": FtnAddress(zone=2, net=1, node=2, domain="fidonet")},
        {"external_id": "hash:sha256:abc"},
        {"external_id": "a", "body_text": "\x01MSGID: b"},
        {"body_text": "\x01CHRS: UTF-8 4"},
        {"body_text": "\x01CHRS: something 2"},
        {"body_text": "\x01MSGID: a\n\x01MSGID: b"},
        {"control_lines": (ControlLine(name="BAD NAME", value="v", raw="ignored"),)},
        {"control_lines": (ControlLine(name="TEST", value="v\nnext", raw="ignored"),)},
        {
            "from_address": FtnAddress(zone=2, net=1, node=2),
            "body_text": "\x01INTL 3:1/2 4:1/2",
        },
    ],
)
def test_rejected_before_creation(tmp_path: Path, changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        MsgWriter().write(tmp_path, [message(**changes)])
    assert list(tmp_path.iterdir()) == []


def test_preserve_and_reconcile_controls(tmp_path: Path) -> None:
    body = "\x01MSGID: 2:1/2 abc\n\x01CHRS: IBMPC 2\n\x01TEST: value\nText"
    MsgWriter().write(
        tmp_path,
        [
            message(
                body_text=body,
                external_id="2:1/2 abc",
                control_lines=(
                    ControlLine(name="TEST", value="value", raw="wrong"),
                    ControlLine(name="REPLY", value="old", raw="wrong"),
                ),
            )
        ],
    )
    raw = (tmp_path / "1.MSG").read_bytes()[190:]
    assert (
        raw == ("\x01REPLY: old\n" + body).replace("\n", "\r").encode("cp850") + b"\x00"
    )


def test_append_and_partial_validation_failure(tmp_path: Path) -> None:
    (tmp_path / "009.mSg").write_bytes(b"untouched")
    (tmp_path / "junk.msg").write_bytes(b"untouched")
    with pytest.raises(ValueError):
        MsgWriter().write(tmp_path, [message(), message(subject="x" * 72)])
    assert (tmp_path / "009.mSg").read_bytes() == b"untouched"
    assert (tmp_path / "junk.msg").read_bytes() == b"untouched"
    assert (tmp_path / "10.MSG").exists()
    assert not (tmp_path / "11.MSG").exists()


def test_exclusive_creation_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.open
    raced = False

    def open_with_race(
        path: Path, mode: str = "r", *args: Any, **kwargs: Any
    ) -> BinaryIO:
        nonlocal raced
        if mode == "xb" and not raced:
            raced = True
            path.write_bytes(b"racer")
        return cast(BinaryIO, original(path, mode, *args, **kwargs))

    monkeypatch.setattr(Path, "open", open_with_race)
    assert MsgWriter().write(tmp_path, [message()]) == 1
    assert (tmp_path / "1.MSG").read_bytes() == b"racer"
    assert (tmp_path / "2.MSG").exists()


@pytest.mark.parametrize("failure", ["write", "short", "close"])
def test_current_file_cleanup_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    original = Path.open

    class FailingFile:
        def __init__(self, handle: BinaryIO) -> None:
            self.handle = handle

        def __enter__(self) -> "FailingFile":
            return self

        def __exit__(self, *args: object) -> None:
            self.handle.close()
            if failure == "close":
                raise OSError("disk failure")

        def write(self, data: bytes) -> int:
            if failure == "close":
                return self.handle.write(data)
            self.handle.write(data[:20])
            if failure == "short":
                return 20
            raise OSError("disk failure")

    def failing_open(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> object:
        handle = original(path, mode, *args, **kwargs)
        if mode == "xb" and path.name == "2.MSG":
            return FailingFile(cast(BinaryIO, handle))
        return handle

    monkeypatch.setattr(Path, "open", failing_open)
    with pytest.raises(OSError):
        MsgWriter().write(tmp_path, [message(), message()])
    assert (tmp_path / "1.MSG").exists()
    assert not (tmp_path / "2.MSG").exists()


@pytest.mark.parametrize(
    "body",
    [
        "\x01INTL broken",
        "\x01FMPT invalid",
        "\x01TOPT -1",
        "\x01FMPT 0\n\x01FMPT 3",
        "\x01INTL 2:1/2 3:1/2\n\x01INTL 4:1/2 3:1/2",
    ],
)
def test_invalid_address_declarations(tmp_path: Path, body: str) -> None:
    with pytest.raises(ValueError):
        MsgWriter().write(tmp_path, [message(body_text=body)])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "name,side", [("FMPT", "from_address"), ("TOPT", "to_address")]
)
def test_explicit_no_point_conflicts(tmp_path: Path, name: str, side: str) -> None:
    with pytest.raises(ValueError):
        MsgWriter().write(
            tmp_path,
            [
                message(
                    body_text=f"\x01{name} 3",
                    **{
                        side: FtnAddress(zone=2, net=1, node=2),
                    },
                )
            ],
        )
    assert list(tmp_path.iterdir()) == []


def test_address_controls_without_model_addresses(tmp_path: Path) -> None:
    body = "\x01INTL 2:1/2 3:1/2\n\x01FMPT 3\nText"
    MsgWriter().write(tmp_path, [message(body_text=body)])
    assert (tmp_path / "1.MSG").read_bytes()[190:] == (
        "\x01CHRS: CP850 2\n" + body
    ).replace("\n", "\r").encode("cp850") + b"\x00"


@pytest.mark.parametrize("charset,level", [("UTF-8", 4), ("ASCII", 1), ("CP850", 2)])
def test_generated_charset_level(tmp_path: Path, charset: str, level: int) -> None:
    MsgWriter().write(
        tmp_path,
        [message(from_name="Sender", body_text="Body")],
        WriterOptions(target_charset=charset),
    )
    assert (tmp_path / "1.MSG").read_bytes()[190:] == (
        f"\x01CHRS: {charset} {level}\rBody\x00".encode("ascii")
    )
