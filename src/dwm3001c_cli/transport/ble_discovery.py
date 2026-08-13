"""Descubrimiento del puente nRF52840 por Bluetooth Low Energy.

Análogo a ``transport/discovery.py`` (USB), pero el mecanismo no tiene nada en
común (escaneo BLE asincrónico vs. enumeración de puertos COM), por eso vive en
un módulo aparte. Solo se usa en la rama ``hardware/ble-bridge-nrf52840``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from bleak import BleakScanner

from dwm3001c_cli.transport.ble_link import NUS_SERVICE_UUID

# [Verificado 2026-08-13] Nombre de advertising real del puente nRF52840
# (configurable en runtime en el firmware puente con "bt name").
DEFAULT_NAME_FILTER = "UWB Node"


@dataclass(frozen=True)
class BleBoardInfo:
    """Puente nRF52840 candidato, visto en un escaneo BLE."""

    address: str
    name: str
    rssi: int | None


async def scan_ble_boards(
    timeout_s: float = 6.0, name_filter: str = DEFAULT_NAME_FILTER
) -> list[BleBoardInfo]:
    """Escanea ``timeout_s`` segundos y devuelve los candidatos encontrados.

    Filtra por nombre de advertising (``name_filter``, si no es vacío) y/o por
    la presencia del UUID de servicio NUS — cualquiera de los dos alcanza,
    porque el nombre es configurable en runtime y no está garantizado.
    """
    found = await BleakScanner.discover(timeout=timeout_s, return_adv=True)
    nus_uuid = NUS_SERVICE_UUID.lower()

    boards: list[BleBoardInfo] = []
    for device, adv in found.values():
        name = device.name or adv.local_name or ""
        matches_name = bool(name_filter) and name_filter in name
        matches_uuid = nus_uuid in (uuid.lower() for uuid in adv.service_uuids)
        if matches_name or matches_uuid:
            boards.append(BleBoardInfo(address=device.address, name=name, rssi=adv.rssi))
    return sorted(boards, key=lambda board: board.name)


def find_ble_boards(
    timeout_s: float = 6.0, name_filter: str = DEFAULT_NAME_FILTER
) -> list[BleBoardInfo]:
    """Envoltorio síncrono de :func:`scan_ble_boards` para la capa ``app``."""
    return asyncio.run(scan_ble_boards(timeout_s=timeout_s, name_filter=name_filter))
