"""Parsers de las salidas del firmware CLI.

Funciones puras: reciben líneas de texto crudas y devuelven modelos tipados.
Ante entrada no reconocible lanzan ``ValueError`` con la línea ofensiva; la capa
superior decide cómo tratarlo.

Los formatos provienen de la guía (docs/referencias/) y de capturas de hardware
real (firmware 1.1.0): el JSON de ``STAT`` puede llegar partido en varias líneas
y sin la línea ``MODE:`` que muestra el manual.
"""

from __future__ import annotations

import json
import re
from typing import Any

from dwm3001c_cli.core.models import CalKey, ChipId, DeviceInfo, Measurement

# Prefijo del bloque JSON de STAT: "JS" + longitud en 4 dígitos hex + "{...".
# Sin ancla "^": por USB directo, cuando STAT se envía con LISTENER/INITF/RESPF
# ya corriendo, el eco del comando llega pegado sin separador delante del
# bloque (p. ej. "STAT\rJS010D{...}", verificado con hardware real fw 1.1.0),
# igual que el caso ya conocido del bridge UART de J9 (ver send_command).
_JS_PREFIX_RE = re.compile(r"JS[0-9A-Fa-f]{4}(?=\{)")
_MODE_RE = re.compile(r"^MODE:\s*(\S+)")

# "clave: 0xVALOR (len: N)" — claves con puntos y underscores (ej. ant0.ch9.ant_delay).
_CALKEY_RE = re.compile(r"^\s*([A-Za-z0-9_.]+):\s*0x([0-9A-Fa-f]+)\s*\(len:\s*(\d+)\)\s*$")

_SESSION_PREFIX = "SESSION_INFO_NTF"
_SESSION_FIELDS = {
    "sequence_number": re.compile(r"sequence_number=(\d+)"),
    "block_index": re.compile(r"block_index=(\d+)"),
    "mac_address": re.compile(r"mac_address=(0x[0-9A-Fa-f]+)"),
    "status": re.compile(r'status="([^"]*)"'),
    "distance_cm": re.compile(r"distance\[cm\]=(-?\d+)"),
    "rssi_dbm": re.compile(r"RSSI\[dBm\]=(-?\d+(?:\.\d+)?)"),
}

_DECAID_FIELDS = {
    "device_id": re.compile(r"Device ID\s*=\s*(\S+)"),
    "lot_id": re.compile(r"Lot ID\s*=\s*(\S+)"),
    "part_id": re.compile(r"Part ID\s*=\s*(\S+)"),
    "soc_id": re.compile(r"SoC ID\s*=\s*(\S+)"),
}


def is_ok(lines: list[str]) -> bool:
    """Indica si alguna línea es el ``ok`` con que el firmware confirma un comando."""
    return any(line.strip().lower() == "ok" for line in lines)


def parse_stat(lines: list[str]) -> DeviceInfo:
    """Parsea la salida de ``STAT``.

    Tolera ambas variantes conocidas:

    - Manual (guía §2.1): línea ``MODE: NONE`` seguida del bloque ``JSxxxx{...}``.
    - Firmware 1.1.0 real: sin línea ``MODE:``, JSON partido en varias líneas y
      ``ok`` final; el modo se deriva de ``Current App``.

    No asume que ``lines`` viene sin eco del comando: por USB directo, si
    ``STAT`` se envía con ``LISTENER``/``INITF``/``RESPF`` ya corriendo, puede
    llegar precedido de salida asíncrona de la app y con el eco pegado sin
    separador justo antes del bloque JSON (``"STAT\\rJS010D{...}"``, verificado
    con hardware real). El bloque ``JSxxxx{`` se busca en cualquier posición de
    la línea, no solo al inicio.
    """
    raw = "\n".join(lines)
    mode: str | None = None
    js_start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if (match := _MODE_RE.match(stripped)) is not None:
            mode = match.group(1)
        if js_start is None and _JS_PREFIX_RE.search(stripped) is not None:
            js_start = index
    if js_start is None:
        raise ValueError(f"Salida de STAT sin bloque JSxxxx: {raw!r}")

    joined = "".join(line.strip() for line in lines[js_start:])
    js_match = _JS_PREFIX_RE.search(joined)
    assert js_match is not None  # ya lo encontramos línea por línea arriba
    json_text = joined[js_match.end() :]
    try:
        decoded, _ = json.JSONDecoder().raw_decode(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON de STAT inválido: {json_text!r}") from exc
    if not isinstance(decoded, dict) or not isinstance(decoded.get("Info"), dict):
        raise ValueError(f"STAT sin objeto 'Info': {json_text!r}")
    info: dict[str, Any] = decoded["Info"]

    def field(key: str) -> str:
        value = info.get(key, "")
        return value if isinstance(value, str) else str(value)

    apps_value = info.get("Apps", [])
    apps = tuple(str(app) for app in apps_value) if isinstance(apps_value, list) else ()

    current_app = field("Current App")
    if mode is None:
        # Firmware 1.1.0: sin línea MODE; con app detenida "Current App" es "NONE".
        mode = current_app
    if not mode:
        raise ValueError(f"STAT sin modo ni 'Current App': {raw!r}")

    return DeviceInfo(
        mode=mode,
        device=field("Device"),
        current_app=current_app,
        version=field("Version"),
        build=field("Build"),
        apps=apps,
        driver=field("Driver"),
        uwb_stack=field("UWB stack"),
        raw=raw,
    )


def parse_calkey_line(line: str) -> CalKey:
    """Parsea una línea ``clave: 0xVALOR (len: N)`` de ``CALKEY``/``LISTCAL``."""
    match = _CALKEY_RE.match(line)
    if match is None:
        raise ValueError(f"Línea de clave de calibración no reconocida: {line!r}")
    return CalKey(
        name=match.group(1),
        value=int(match.group(2), 16),
        length_bytes=int(match.group(3)),
        raw=line,
    )


def parse_listcal(lines: list[str]) -> dict[str, CalKey]:
    """Parsea la salida de ``LISTCAL`` en un mapa nombre → clave.

    Ignora líneas vacías, el eco del comando (una sola palabra en mayúsculas) y
    el ``ok`` final. Cualquier otra línea no reconocida es un error.
    """
    keys: dict[str, CalKey] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.lower() == "ok" or stripped.isupper():
            continue
        cal_key = parse_calkey_line(line)
        keys[cal_key.name] = cal_key
    return keys


def parse_session_info(line: str) -> Measurement:
    """Parsea una notificación ``SESSION_INFO_NTF`` (guía §2.4).

    ``distance_cm`` y ``rssi_dbm`` son opcionales; el resto de los campos es
    obligatorio y su ausencia es un error.
    """
    if not line.strip().startswith(_SESSION_PREFIX):
        raise ValueError(f"No es una notificación {_SESSION_PREFIX}: {line!r}")

    values: dict[str, str | None] = {
        name: (match.group(1) if (match := pattern.search(line)) else None)
        for name, pattern in _SESSION_FIELDS.items()
    }
    for required in ("sequence_number", "block_index", "mac_address", "status"):
        if values[required] is None:
            raise ValueError(f"{_SESSION_PREFIX} sin campo {required}: {line!r}")

    distance = values["distance_cm"]
    rssi = values["rssi_dbm"]
    return Measurement(
        sequence_number=int(values["sequence_number"] or 0),
        block_index=int(values["block_index"] or 0),
        mac_address=values["mac_address"] or "",
        status=values["status"] or "",
        distance_cm=int(distance) if distance is not None else None,
        rssi_dbm=float(rssi) if rssi is not None else None,
        raw=line,
    )


def parse_decaid(lines: list[str]) -> ChipId:
    """Parsea la salida de ``DECAID`` (guía §2.2)."""
    text = "\n".join(lines)
    values: dict[str, str] = {}
    for name, pattern in _DECAID_FIELDS.items():
        match = pattern.search(text)
        if match is None:
            raise ValueError(f"Salida de DECAID sin campo {name}: {text!r}")
        values[name] = match.group(1)
    return ChipId(**values)
