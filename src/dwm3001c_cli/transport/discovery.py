"""Descubrimiento de placas DWM3001CDK entre los puertos COM del sistema.

La placa expone dos interfaces USB posibles (guía §1.2):

- Conector J9 (*interface MCU*): puerto COM del J-Link OB de SEGGER, VID 0x1366.
- Conector J20 (*nRF USB*): puerto COM directo del nRF52833, VID 0x1915 (Nordic).

[Fuera del manual] Los VID son los registrados por SEGGER y Nordic Semiconductor;
se confirman contra hardware real en la fase F6.
"""

from __future__ import annotations

from dataclasses import dataclass

from serial.tools import list_ports

# VID USB → interfaz de la placa a la que corresponde.
KNOWN_VIDS: dict[int, str] = {
    0x1366: "jlink-uart",  # SEGGER J-Link OB (conector J9)
    0x1915: "nrf-usb",  # Nordic nRF USB (conector J20)
}


@dataclass(frozen=True)
class BoardPort:
    """Puerto COM candidato a ser una placa DWM3001CDK."""

    port: str
    description: str
    serial_number: str | None
    interface_hint: str


def find_boards() -> list[BoardPort]:
    """Enumera los puertos COM y devuelve los candidatos a DWM3001CDK.

    Filtra por VID USB conocidos (SEGGER y Nordic). Devuelve la lista ordenada
    por nombre de puerto; vacía si no hay candidatos (la capa de aplicación
    decide el mensaje al usuario).
    """
    boards = [
        BoardPort(
            port=info.device,
            description=info.description or "",
            serial_number=info.serial_number,
            interface_hint=KNOWN_VIDS[info.vid],
        )
        for info in list_ports.comports()
        if info.vid in KNOWN_VIDS
    ]
    return sorted(boards, key=lambda board: board.port)
