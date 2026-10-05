"""Session mutations and controlled faults against independently inspected bytes."""

import os
import struct
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from golded_ftn import (
    ConflictError,
    ControlLine,
    FtnAddress,
    LockTimeoutError,
    MessagePatch,
    OutgoingMessage,
    ParserException,
    RollbackError,
    UnsupportedOperationError,
    WriterError,
    WriterOptions,
)
from golded_ftn._writer_io import IO

from golded_ftn_msg import MsgWriter


def message(body: str = "body") -> OutgoingMessage:
    return OutgoingMessage(
        from_name="sender",
        to_name="recipient",
        subject="subject",
        body_text=body,
        external_id="2:1/2 id",
        reply_to_msgno=6,
        reply1st_msgno=7,
    )


def snapshot(base: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in base.iterdir() if p.is_file()}


@pytest.mark.parametrize("operation", ["append", "update"])
@pytest.mark.parametrize("source", ["body", "controls"])
def test_conflicting_reply_rejected_without_mutation(
    tmp_path: Path, operation: str, source: str
) -> None:
    body = "text"
    controls: tuple[ControlLine, ...] = ()
    if source == "body":
        body = "\x01REPLY: first\n\x01REPLY: second\ntext"
    else:
        controls = tuple(
            ControlLine(name="REPLY", value=value, raw="")
            for value in ("first", "second")
        )
    with MsgWriter().open(tmp_path) as session:
        existing = session.append(message())
        before = snapshot(tmp_path)
        with pytest.raises(ParserException, match="Conflicting REPLY metadata"):
            if operation == "append":
                session.append(replace(message(body), control_lines=controls))
            else:
                session.update(
                    existing.identity,
                    MessagePatch(control_lines=controls)
                    if source == "controls"
                    else MessagePatch(body_text=body),
                    existing.revision,
                )
        assert snapshot(tmp_path) == before
        assert session.read(1).revision == existing.revision


def test_body_edit_preserves_raw_unknown_controls(tmp_path: Path) -> None:
    header = bytearray(190)
    header[:6] = b"Alice\0"
    header[36:40] = b"Bob\0"
    original = b"\x01CHRS: CP850 2\r\x01MSGID-OTHER:  spaced  \r\x01UNKNOWN   value  \r"
    path = tmp_path / "1.MSG"
    path.write_bytes(bytes(header) + original + b"old\0")
    with MsgWriter().open(tmp_path) as session:
        loaded = session.read(1)
        changed = session.update(
            loaded.identity, MessagePatch(body_text="new"), loaded.revision
        )
        assert original in path.read_bytes()
        session.update(
            changed.identity, MessagePatch(external_id=None), changed.revision
        )
        assert b"\x01MSGID-OTHER:  spaced  \r" in path.read_bytes()


def test_two_controlled_processes_append_without_clobber(tmp_path: Path) -> None:
    program = """
import sys
from golded_ftn import OutgoingMessage
from golded_ftn_msg import MsgWriter
with MsgWriter().open(sys.argv[1]) as session:
    print('ready', flush=True)
    sys.stdin.readline()
    result = session.append(OutgoingMessage(
        from_name='Alice', to_name='Bob', subject='Demo', body_text='text'))
    print(result.identity.msgno, flush=True)
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", program, str(tmp_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    try:
        for child in children:
            assert child.stdout is not None and child.stdout.readline() == "ready\n"
        for child in children:
            assert child.stdin is not None
            child.stdin.write("append\n")
            child.stdin.flush()
        numbers = []
        for child in children:
            output, _ = child.communicate(timeout=5)
            assert child.returncode == 0
            numbers.append(int(output))
        assert sorted(numbers) == [1, 2]
        with MsgWriter().open(tmp_path) as session:
            assert session.read(1).message.subject == "Demo"
            assert session.read(2).message.subject == "Demo"
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)


def test_forced_exit_leaves_private_temp_but_no_published_message(
    tmp_path: Path,
) -> None:
    with MsgWriter().open(tmp_path) as session:
        session.append(message())
    original = (tmp_path / "1.MSG").read_bytes()
    program = """
import os, sys
from golded_ftn import OutgoingMessage
from golded_ftn._writer_io import IO
from golded_ftn_msg import MsgWriter
class ExitIO(IO):
    def write(self, fd, offset, data):
        super().write(fd, offset, data)
        os._exit(23)
with MsgWriter().open(sys.argv[1]) as session:
    session._io = ExitIO()
    session.append(OutgoingMessage(
        from_name='Alice', to_name='Bob', subject='Demo', body_text='text'))
"""
    child = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)], timeout=5, check=False
    )
    assert child.returncode == 23
    assert (tmp_path / "1.MSG").read_bytes() == original
    assert not (tmp_path / "2.MSG").exists()
    assert len(list(tmp_path.glob(".golded-ftn-msg-*"))) == 1


def test_session_lifecycle_and_revision_conflicts(tmp_path: Path) -> None:
    writer = MsgWriter()
    writer.create(tmp_path)
    with writer.open(tmp_path) as left, writer.open(tmp_path) as right:
        first = left.append(message())
        second = right.append(message())
        assert first.revision == left.read(1).revision
        raw = (tmp_path / "1.MSG").read_bytes()
        assert struct.unpack_from("<H", raw, 184)[0] == 6
        assert struct.unpack_from("<H", raw, 188)[0] == 7
        changed = left.update(
            first.identity, MessagePatch(body_text="longer\nbody"), first.revision
        )
        assert right.read(1).message.subject == "subject"
        assert "longer\nbody" in right.read(1).message.body_text
        with pytest.raises(ConflictError):
            right.update(first.identity, MessagePatch(subject="stale"), first.revision)
        assert second.revision == left.read(2).revision
        right.delete(second.identity, second.revision)
        with pytest.raises(ConflictError):
            left.delete(second.identity, second.revision)
        assert left.append(message()).identity.msgno == 3
        left.delete(changed.identity, changed.revision)
    with pytest.raises(WriterError):
        left.read(1)
    with pytest.raises(FileExistsError):
        writer.create(tmp_path)
    with pytest.raises(UnsupportedOperationError):
        writer.open(tmp_path, WriterOptions(concurrent=True))
    with pytest.raises(UnsupportedOperationError):
        writer.open(tmp_path, header_format="opus")


def test_independent_header_preservation(tmp_path: Path) -> None:
    # Explicit FTSC offsets; noncanonical date and padding must survive an update.
    header = bytearray(190)
    header[:6] = b"Alice\0"
    header[6:36] = b"x" * 30
    header[36:40] = b"Bob\0"
    header[72:80] = b"subject\0"
    header[144:164] = b"unparsed-date" + bytes(7)
    for offset, value in ((164, 11), (170, 123), (184, 19), (188, 20), (186, 0x8000)):
        struct.pack_into("<H", header, offset, value)
    body = b"\x01CHRS: CP850 2\r\x01UNKNOWN: retained\rbody\r\0\0"
    (tmp_path / "002.mSg").write_bytes(bytes(header) + body)
    (tmp_path / "LASTREAD").write_bytes(b"existing lastread")
    with MsgWriter().open(tmp_path) as session:
        read = session.read(2)
        changed = session.update(
            read.identity, MessagePatch(attributes_raw=1), read.revision
        )
        raw = (tmp_path / "002.mSg").read_bytes()
        expected = bytearray(header)
        expected[186:188] = b"\x01\x00"
        assert raw == bytes(expected) + body
        renamed = session.update(
            changed.identity, MessagePatch(subject="new"), changed.revision
        )
        raw = (tmp_path / "002.mSg").read_bytes()
        assert raw[:72] == expected[:72]
        assert raw[144:186] == expected[144:186]
        assert raw[188:] == bytes(expected[188:]) + body
        assert session.read(2).revision == renamed.revision
    assert (tmp_path / "LASTREAD").read_bytes() == b"existing lastread"


def test_clear_controls_routing_addresses_and_links(tmp_path: Path) -> None:
    writer = MsgWriter()
    outgoing = OutgoingMessage(
        from_name="a",
        to_name="b",
        subject="c",
        body_text="text",
        from_address=FtnAddress(zone=2, net=1, node=2, point=3),
        to_address=FtnAddress(zone=2, net=1, node=4),
        external_id="id",
        control_lines=(ControlLine(name="CUSTOM", value="value", raw=""),),
        routing_seen_by=("1/2",),
        routing_path=("1/4",),
    )
    with writer.open(tmp_path) as session:
        first = session.append(outgoing)
        changed = session.update(
            first.identity,
            MessagePatch(
                external_id=None,
                from_address=None,
                routing_seen_by=None,
                routing_path=None,
                reply_to_msgno=None,
                reply1st_msgno=22,
            ),
            first.revision,
        )
        read = session.read(1).message
        assert read.from_address is None
        assert read.reply_to_msgno is None and read.reply1st_msgno == 22
        assert read.control_lines is not None
        assert read.control_lines.msgid is None
        assert read.control_lines.seen_by == () and read.control_lines.path == ()
        assert any(c.name == "CUSTOM" for c in read.control_lines.kludges)
        assert session.read(1).revision == changed.revision


@pytest.mark.parametrize(
    "operation,steps", [("append", 5), ("update", 3), ("delete", 3)]
)
def test_fault_boundaries(tmp_path: Path, operation: str, steps: int) -> None:
    for step in range(1, steps + 1):
        base = tmp_path / str(step)
        writer = MsgWriter()
        writer.create(base)

        class FailOnce(IO):
            count = 0

            def __init__(self, fail_step: int) -> None:
                self.fail_step = fail_step

            def fail(self) -> None:
                self.count += 1
                if self.count == self.fail_step:
                    raise OSError("injected")

            def write(self, fd: int, offset: int, data: bytes) -> None:
                self.fail()
                super().write(fd, offset, data)

            def flush(self, fd: int) -> None:
                self.fail()
                super().flush(fd)

        with writer.open(base) as session:
            first = session.append(message())
            before = snapshot(base)
            session._io = FailOnce(step)
            with pytest.raises(OSError, match="injected"):
                if operation == "append":
                    session.append(message())
                elif operation == "update":
                    session.update(
                        first.identity,
                        MessagePatch(body_text="changed"),
                        first.revision,
                    )
                else:
                    session.delete(first.identity, first.revision)
            assert snapshot(base) == before
            assert session.read(1).revision == first.revision


def test_failed_rollback_poisons_session(tmp_path: Path) -> None:
    class FailAfterPublish(IO):
        count = 0

        def write(self, fd: int, offset: int, data: bytes) -> None:
            self.count += 1
            if self.count >= 2:
                raise OSError("permanent failure")
            super().write(fd, offset, data)

    with MsgWriter().open(tmp_path) as session:
        session._io = FailAfterPublish()
        with pytest.raises(RollbackError, match="append.*rollback"):
            session.append(message())
        with pytest.raises(WriterError, match="unusable"):
            session.read(1)


def test_external_lock_timeout(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX controlled helper")
    writer = MsgWriter()
    with writer.open(tmp_path) as session:
        helper = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import fcntl,sys; f=open(sys.argv[1],'r+b'); "
                "fcntl.lockf(f,fcntl.LOCK_EX,1,0); print('locked',flush=True); "
                "sys.stdin.readline()",
                str(session._lock_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert helper.stdout is not None
            assert helper.stdout.readline() == "locked\n"
            with writer.open(tmp_path, WriterOptions(lock_timeout=0.02)) as timed:
                with pytest.raises(LockTimeoutError):
                    timed.append(message())
        finally:
            helper.communicate("release\n", timeout=5)
        assert session.append(message()).identity.msgno == 1


def test_corrupt_base_and_invalid_patch(tmp_path: Path) -> None:
    with MsgWriter().open(tmp_path) as session:
        first = session.append(message())
        before = snapshot(tmp_path)
        for patch in (
            MessagePatch(from_name=None),
            MessagePatch(body_text=None),
            MessagePatch(reply_to_msgno=65536),
            MessagePatch(reply_list=(1,)),
            MessagePatch(subject="😀"),
        ):
            with pytest.raises(ValueError):
                session.update(first.identity, patch, first.revision)
            assert snapshot(tmp_path) == before
        (tmp_path / "1.MSG").write_bytes(b"bad")
        with pytest.raises(ParserException):
            session.append(message())
