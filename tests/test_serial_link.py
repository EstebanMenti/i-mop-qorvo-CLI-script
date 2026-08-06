"""Tests de la capa de transporte serie (fase F1)."""

from collections import deque

import pytest
import serial

import dwm3001c_cli.transport.serial_link as serial_link_module
from dwm3001c_cli.core.errors import TransportError
from dwm3001c_cli.transport.serial_link import LineAssembler, SerialLink


class FakeSerial:
    """Reemplazo mínimo de ``serial.Serial`` para tests sin hardware."""

    def __init__(self, rx_chunks: list[bytes] | None = None) -> None:
        self.rx_chunks = deque(rx_chunks or [])
        self.written = b""
        self.closed = False

    @property
    def in_waiting(self) -> int:
        return len(self.rx_chunks[0]) if self.rx_chunks else 0

    def read(self, size: int = 1) -> bytes:
        return self.rx_chunks.popleft() if self.rx_chunks else b""

    def write(self, data: bytes) -> int:
        self.written += data
        return len(data)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_serial(monkeypatch: pytest.MonkeyPatch) -> FakeSerial:
    """Instala un FakeSerial en lugar de serial.Serial y lo devuelve."""
    fake = FakeSerial()
    monkeypatch.setattr(serial_link_module.serial, "Serial", lambda **kwargs: fake)
    return fake


class TestLineAssembler:
    def test_partial_bytes_across_feeds(self) -> None:
        assembler = LineAssembler()

        assert assembler.feed(b"MODE: N") == []
        assert assembler.feed(b"ONE\r\nok\r\n") == ["MODE: NONE", "ok"]

    def test_multiple_lines_in_one_chunk(self) -> None:
        assembler = LineAssembler()

        lines = assembler.feed(b"linea1\r\nlinea2\nlinea3\r\n")

        assert lines == ["linea1", "linea2", "linea3"]

    def test_non_ascii_bytes_are_replaced_not_fatal(self) -> None:
        assembler = LineAssembler()

        lines = assembler.feed(b"ok\xff\r\n")

        assert len(lines) == 1
        assert lines[0].startswith("ok")


class TestSerialLink:
    def test_open_failure_raises_transport_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_serial_exception(**kwargs: object) -> None:
            raise serial.SerialException("no existe")

        monkeypatch.setattr(serial_link_module.serial, "Serial", raise_serial_exception)

        link = SerialLink("COM99")
        with pytest.raises(TransportError, match="COM99"):
            link.open()

    def test_write_line_appends_terminator(self, fake_serial: FakeSerial) -> None:
        with SerialLink("COM7") as link:
            link.write_line("STAT")

        assert fake_serial.written == b"STAT\r\n"

    def test_read_line_assembles_chunks(self, fake_serial: FakeSerial) -> None:
        fake_serial.rx_chunks.extend([b"MODE: N", b"ONE\r\n"])

        with SerialLink("COM7") as link:
            assert link.read_line(timeout_s=1.0) == "MODE: NONE"

    def test_read_line_timeout_returns_none(self, fake_serial: FakeSerial) -> None:
        with SerialLink("COM7") as link:
            assert link.read_line(timeout_s=0.05) is None

    def test_operations_on_closed_port_raise(self) -> None:
        link = SerialLink("COM7")

        with pytest.raises(TransportError, match="no está abierto"):
            link.write_line("STAT")
        with pytest.raises(TransportError, match="no está abierto"):
            link.read_line(timeout_s=0.1)

    def test_context_manager_closes_port(self, fake_serial: FakeSerial) -> None:
        with SerialLink("COM7"):
            pass

        assert fake_serial.closed
