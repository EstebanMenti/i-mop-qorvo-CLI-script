"""Modelos de datos tipados del dominio.

Dataclasses inmutables que representan las respuestas parseadas del firmware
CLI. Todas conservan la entrada cruda (``raw``) cuando aplica, para diagnóstico.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceInfo:
    """Información reportada por ``STAT`` (guía §2.1).

    ``mode`` se toma de la línea ``MODE:`` si el firmware la emite; si no
    (caso del firmware 1.1.0 real), se deriva del campo ``Current App``.
    """

    mode: str
    device: str
    current_app: str
    version: str
    build: str
    apps: tuple[str, ...]
    driver: str
    uwb_stack: str
    raw: str


@dataclass(frozen=True)
class CalKey:
    """Una clave de calibración, según la salida de ``CALKEY``/``LISTCAL`` (guía §2.3)."""

    name: str
    value: int
    length_bytes: int
    raw: str


@dataclass(frozen=True)
class Measurement:
    """Una medición de una notificación ``SESSION_INFO_NTF`` (guía §2.4).

    ``distance_cm`` y ``rssi_dbm`` pueden faltar: la distancia no se reporta si
    la ronda falló, y el RSSI solo aparece con ``DIAG 1`` habilitado.
    """

    sequence_number: int
    block_index: int
    mac_address: str
    status: str
    distance_cm: int | None
    rssi_dbm: float | None
    raw: str


@dataclass(frozen=True)
class RangingStats:
    """Estadísticas de un lote de mediciones TWR."""

    n_requested: int
    n_received: int
    n_success: int
    mean_cm: float
    std_cm: float
    min_cm: int
    max_cm: int


@dataclass(frozen=True)
class ChipId:
    """Identificación del chip UWB reportada por ``DECAID`` (guía §2.2)."""

    device_id: str
    lot_id: str
    part_id: str
    soc_id: str


@dataclass(frozen=True)
class ValidationResult:
    """Resultado de un check de la suite de validación de comandos."""

    command: str
    sent: str
    passed: bool
    detail: str
    response_lines: tuple[str, ...]
    duration_s: float
