"""Capa de transporte serie: interfaz ``Transport`` e implementación ``SerialLink``.

Esta capa solo mueve líneas de texto por el puerto serie; no conoce el protocolo
CLI del firmware (eso es responsabilidad de ``core``). Todo el tráfico crudo
TX/RX se registra en el log a nivel DEBUG para diagnóstico.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from types import TracebackType
from typing import Protocol, Self

import serial

from dwm3001c_cli.core.errors import TransportError

logger = logging.getLogger(__name__)

BAUD_RATE = 115200
# Parámetros fijos del firmware CLI: 115200 8N1 sin control de flujo (guía §1.2).

# TODO(verificar-con-hardware): confirmar en F6 si el firmware acepta "\r\n" o
# requiere solo "\r" como terminador de línea.
LINE_TERMINATOR = b"\r\n"


class Transport(Protocol):
    """Contrato mínimo de un transporte de líneas hacia la placa.

    Lo implementan ``SerialLink`` (hardware real) y el ``FakeTransport`` de los
    tests (simulación sin hardware).
    """

    def open(self) -> None: ...

    def close(self) -> None: ...

    def write_line(self, line: str) -> None: ...

    def read_line(self, timeout_s: float) -> str | None:
        """Devuelve la próxima línea recibida, o ``None`` si venció el timeout."""
        ...

    @property
    def name(self) -> str:
        """Identificador del transporte, p. ej. ``"COM7"``."""
        ...


class LineAssembler:
    """Arma líneas completas a partir de fragmentos de bytes recibidos.

    El puerto serie entrega los datos en trozos arbitrarios; esta clase acumula
    los bytes parciales entre llamadas y devuelve solo líneas terminadas en
    ``\\n`` (descartando ``\\r``). La decodificación es ASCII con reemplazo,
    porque el firmware CLI emite texto plano.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[str]:
        """Ingresa un fragmento y devuelve las líneas completas disponibles."""
        self._buffer.extend(data)
        lines: list[str] = []
        while (newline_at := self._buffer.find(b"\n")) >= 0:
            raw = bytes(self._buffer[:newline_at])
            del self._buffer[: newline_at + 1]
            lines.append(raw.decode("ascii", errors="replace").rstrip("\r"))
        return lines


class SerialLink:
    """Transporte serie real sobre ``pyserial``.

    Uso típico como context manager::

        with SerialLink("COM7") as link:
            link.write_line("STAT")
            line = link.read_line(timeout_s=2.0)

    Args:
        port: nombre del puerto, p. ej. ``"COM7"``.
        read_timeout_s: timeout de cada lectura física del puerto; acota la
            latencia del bucle de ``read_line`` sin ocupar CPU.
    """

    def __init__(self, port: str, *, read_timeout_s: float = 0.1) -> None:
        self._port_name = port
        self._read_timeout_s = read_timeout_s
        self._serial: serial.Serial | None = None
        self._assembler = LineAssembler()
        self._pending: deque[str] = deque()

    @property
    def name(self) -> str:
        return self._port_name

    def open(self) -> None:
        """Abre el puerto con los parámetros fijos del firmware CLI (115200 8N1)."""
        if self._serial is not None:
            return
        try:
            self._serial = serial.Serial(
                port=self._port_name,
                baudrate=BAUD_RATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self._read_timeout_s,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
        except serial.SerialException as exc:
            raise TransportError(f"No se pudo abrir el puerto {self._port_name}: {exc}") from exc
        logger.debug("Puerto %s abierto (%d baudios, 8N1)", self._port_name, BAUD_RATE)

    def close(self) -> None:
        if self._serial is None:
            return
        try:
            self._serial.close()
        finally:
            self._serial = None
            logger.debug("Puerto %s cerrado", self._port_name)

    def write_line(self, line: str) -> None:
        port = self._require_open()
        logger.debug("TX %s: %s", self._port_name, line)
        try:
            port.write(line.encode("ascii") + LINE_TERMINATOR)
        except serial.SerialException as exc:
            raise TransportError(
                f"Falla al escribir en {self._port_name} (¿placa desconectada?): {exc}"
            ) from exc

    def read_line(self, timeout_s: float) -> str | None:
        """Devuelve la próxima línea completa, o ``None`` si venció ``timeout_s``."""
        port = self._require_open()
        deadline = time.monotonic() + timeout_s
        while True:
            if self._pending:
                line = self._pending.popleft()
                logger.debug("RX %s: %s", self._port_name, line)
                return line
            if time.monotonic() >= deadline:
                return None
            try:
                chunk = port.read(max(1, port.in_waiting))
            except serial.SerialException as exc:
                raise TransportError(
                    f"Falla al leer de {self._port_name} (¿placa desconectada?): {exc}"
                ) from exc
            if chunk:
                self._pending.extend(self._assembler.feed(chunk))

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _require_open(self) -> serial.Serial:
        if self._serial is None:
            raise TransportError(f"El puerto {self._port_name} no está abierto")
        return self._serial
