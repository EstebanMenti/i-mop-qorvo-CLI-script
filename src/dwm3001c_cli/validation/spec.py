"""Especificación declarativa de la suite de validación de comandos (plan §5.1).

Tres grupos, ejecutados en orden:

- **A** — solo lectura, sin efectos.
- **B** — modifican estado con restauración inmediata.
- **C** — comandos de aplicación (requieren ``STOP`` al final; C4 necesita dos placas).

Quedan **fuera** de la suite por diseño: ``RESTORE`` (destructivo, solo manual con
confirmación), ``UART 0/1`` (riesgo de perder la consola) y la escritura de OTP.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from dwm3001c_cli.core.client import DwmCliClient


class CheckFailedError(Exception):
    """Fallo de un check, con las líneas de respuesta que lo evidencian."""

    def __init__(self, detail: str, response_lines: list[str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.response_lines = response_lines or []


@dataclass
class CheckContext:
    """Recursos y parámetros compartidos por los checks durante una corrida."""

    client: DwmCliClient
    second_client: DwmCliClient | None = None
    settle_delay_s: float = 3.0
    ranging_window_s: float = 10.0


CheckRunner = Callable[[CheckContext], tuple[str, list[str]]]


@dataclass(frozen=True)
class CommandCheck:
    """Un check de la suite: qué se ejecuta, cómo se evalúa y cómo se limpia."""

    check_id: str
    command: str
    sent: str
    run: CheckRunner
    cleanup: Callable[[CheckContext], None] | None = None
    needs_second_board: bool = False


def _require(condition: bool, detail: str, lines: list[str]) -> None:
    if not condition:
        raise CheckFailedError(detail, lines)


def _require_nonempty(lines: list[str], command: str) -> None:
    _require(bool(lines), f"{command} no devolvió ninguna línea", lines)


# ------------------------------------------------------------------- Grupo A


def _a1_help(ctx: CheckContext) -> tuple[str, list[str]]:
    lines = ctx.client.help_cmd()
    _require_nonempty(lines, "HELP")
    joined = " ".join(lines).upper()
    missing = [cmd for cmd in ("STAT", "STOP", "SAVE") if cmd not in joined]
    _require(not missing, f"HELP no menciona {missing}", lines)
    return (f"lista de comandos con {len(lines)} líneas", lines)


def _a2_help_initf(ctx: CheckContext) -> tuple[str, list[str]]:
    lines = ctx.client.help_cmd("INITF")
    _require_nonempty(lines, "HELP INITF")
    _require("INITF" in " ".join(lines).upper(), "HELP INITF no menciona INITF", lines)
    return ("ayuda específica de INITF disponible", lines)


def _a3_stat(ctx: CheckContext) -> tuple[str, list[str]]:
    info = ctx.client.stat()
    lines = info.raw.splitlines()
    _require(info.mode == "NONE", f"modo esperado NONE, reportado {info.mode!r}", lines)
    expected_apps = {"INITF", "RESPF", "LISTENER"}
    _require(
        expected_apps.issubset(set(info.apps)),
        f"apps esperadas {sorted(expected_apps)}, reportadas {list(info.apps)}",
        lines,
    )
    return (f"fw {info.version} ({info.build}); apps {list(info.apps)}", lines)


def _a4_thread(ctx: CheckContext) -> tuple[str, list[str]]:
    lines = ctx.client.thread()
    _require_nonempty(lines, "THREAD")
    return (f"información de hilos con {len(lines)} líneas", lines)


def _a5_decaid(ctx: CheckContext) -> tuple[str, list[str]]:
    chip = ctx.client.decaid()
    lines = [chip.device_id, chip.lot_id, chip.part_id, chip.soc_id]
    _require(
        chip.device_id.lower().startswith("0xdeca"),
        f"Device ID no es de la familia DW3xxx: {chip.device_id}",
        lines,
    )
    return (f"Device ID {chip.device_id}, Part ID {chip.part_id}", lines)


def _a6_getotp(ctx: CheckContext) -> tuple[str, list[str]]:
    lines = ctx.client.getotp()
    _require_nonempty(lines, "GETOTP")
    joined = " ".join(lines).lower()
    # 0x01A y 0x01C: retardos de antena de canal 5 y 9 (guía §1.4).
    _require(
        "0x01a" in joined and "0x01c" in joined,
        "GETOTP no muestra las direcciones 0x01A/0x01C de retardos de antena",
        lines,
    )
    return (f"volcado OTP con {len(lines)} líneas", lines)


def _a7_listcal(ctx: CheckContext) -> tuple[str, list[str]]:
    keys = ctx.client.listcal()
    lines = [key.raw for key in keys.values()]
    _require(len(keys) >= 10, f"LISTCAL devolvió solo {len(keys)} claves (<10)", lines)
    _require(
        "ant0.ch9.ant_delay" in keys,
        "falta la clave ant0.ch9.ant_delay (calibración de distancia, canal 9)",
        lines,
    )
    ant_delay = keys["ant0.ch9.ant_delay"]
    return (f"{len(keys)} claves; ant0.ch9.ant_delay=0x{ant_delay.value:X}", lines)


def _a8_calkey_read(ctx: CheckContext) -> tuple[str, list[str]]:
    cal_key = ctx.client.calkey_read("xtal_trim")
    _require(
        cal_key.length_bytes == 1,
        f"xtal_trim: longitud esperada 1 byte, reportada {cal_key.length_bytes}",
        [cal_key.raw],
    )
    return (f"xtal_trim=0x{cal_key.value:X} (len {cal_key.length_bytes})", [cal_key.raw])


def _a9_uart(ctx: CheckContext) -> tuple[str, list[str]]:
    lines = ctx.client.uart_status()
    _require_nonempty(lines, "UART")
    return ("consulta de estado UART respondida", lines)


def _a10_diag_query(ctx: CheckContext) -> tuple[str, list[str]]:
    lines = ctx.client.send_command("DIAG")
    _require_nonempty(lines, "DIAG")
    return ("consulta de modo diagnóstico respondida", lines)


def _a11_lcfg(ctx: CheckContext) -> tuple[str, list[str]]:
    lines = ctx.client.lcfg()
    _require_nonempty(lines, "LCFG")
    _require("CHAN" in " ".join(lines).upper(), "LCFG no muestra el parámetro CHAN", lines)
    return ("configuración de LISTENER disponible", lines)


# ------------------------------------------------------------------- Grupo B


def _b1_diag_toggle(ctx: CheckContext) -> tuple[str, list[str]]:
    ctx.client.diag(True)
    lines = ctx.client.send_command("DIAG")
    _require("1" in " ".join(lines), "DIAG no refleja el valor 1 tras DIAG 1", lines)
    return ("modo diagnóstico habilitado y verificado", lines)


def _b1_cleanup(ctx: CheckContext) -> None:
    ctx.client.diag(False)


def _b2_calkey_rewrite(ctx: CheckContext) -> tuple[str, list[str]]:
    # Escribe el valor que la clave ya tiene: efecto neto nulo (plan §5.1 B2).
    current = ctx.client.calkey_read("xtal_trim")
    written = ctx.client.calkey_write("xtal_trim", current.value)
    return (f"xtal_trim reescrito con su propio valor ({current.value})", [written.raw])


def _b3_setapp_save(ctx: CheckContext) -> tuple[str, list[str]]:
    ctx.client.setapp("NONE")
    ctx.client.save()
    return ("SETAPP NONE + SAVE confirmados (estado deseado del banco)", [])


# ------------------------------------------------------------------- Grupo C


def _app_check(app: str, starter: Callable[[DwmCliClient], None]) -> CheckRunner:
    def run(ctx: CheckContext) -> tuple[str, list[str]]:
        starter(ctx.client)
        time.sleep(ctx.settle_delay_s)
        info = ctx.client.stat()
        lines = info.raw.splitlines()
        _require(
            info.current_app == app or info.mode == app,
            f"STAT no reporta {app}: modo={info.mode!r}, app={info.current_app!r}",
            lines,
        )
        return (f"aplicación {app} corriendo", lines)

    return run


def _cleanup_stop(ctx: CheckContext) -> None:
    ctx.client.ensure_mode_none()


def _c4_twr_session(ctx: CheckContext) -> tuple[str, list[str]]:
    second = ctx.second_client
    assert second is not None  # el runner saltea el check si no hay segunda placa
    second.ensure_mode_none()
    ctx.client.ensure_mode_none()
    # Orden de arranque: primero el responder, después el initiator (guía §4.2).
    second.start_respf()
    ctx.client.start_initf()
    measurements = ctx.client.read_notifications(duration_s=ctx.ranging_window_s)
    lines = [measurement.raw for measurement in measurements[:10]]
    successes = [
        measurement
        for measurement in measurements
        if measurement.status == "SUCCESS" and measurement.distance_cm is not None
    ]
    _require(
        bool(successes),
        f"sin mediciones SUCCESS en {ctx.ranging_window_s:.0f} s "
        f"({len(measurements)} notificaciones recibidas)",
        lines,
    )
    return (
        f"{len(successes)}/{len(measurements)} mediciones SUCCESS; "
        f"ejemplo: {successes[0].distance_cm} cm",
        lines,
    )


def _c4_cleanup(ctx: CheckContext) -> None:
    ctx.client.ensure_mode_none()
    if ctx.second_client is not None:
        ctx.second_client.ensure_mode_none()


ALL_CHECKS: tuple[CommandCheck, ...] = (
    CommandCheck("A1", "A1 HELP", "HELP", _a1_help),
    CommandCheck("A2", "A2 HELP INITF", "HELP INITF", _a2_help_initf),
    CommandCheck("A3", "A3 STAT", "STAT", _a3_stat),
    CommandCheck("A4", "A4 THREAD", "THREAD", _a4_thread),
    CommandCheck("A5", "A5 DECAID", "DECAID", _a5_decaid),
    CommandCheck("A6", "A6 GETOTP", "GETOTP", _a6_getotp),
    CommandCheck("A7", "A7 LISTCAL", "LISTCAL", _a7_listcal),
    CommandCheck("A8", "A8 CALKEY (lectura)", "CALKEY xtal_trim", _a8_calkey_read),
    CommandCheck("A9", "A9 UART (consulta)", "UART", _a9_uart),
    CommandCheck("A10", "A10 DIAG (consulta)", "DIAG", _a10_diag_query),
    CommandCheck("A11", "A11 LCFG", "LCFG", _a11_lcfg),
    CommandCheck("B1", "B1 DIAG (toggle)", "DIAG 1 → DIAG → DIAG 0", _b1_diag_toggle, _b1_cleanup),
    CommandCheck(
        "B2",
        "B2 CALKEY (escritura neutra)",
        "CALKEY xtal_trim <valor actual>",
        _b2_calkey_rewrite,
    ),
    CommandCheck("B3", "B3 SETAPP + SAVE", "SETAPP NONE → SAVE", _b3_setapp_save),
    CommandCheck(
        "C1",
        "C1 LISTENER",
        "LISTENER → STAT → STOP",
        _app_check("LISTENER", lambda client: client.start_listener()),
        _cleanup_stop,
    ),
    CommandCheck(
        "C2",
        "C2 INITF",
        "INITF → STAT → STOP",
        _app_check("INITF", lambda client: client.start_initf()),
        _cleanup_stop,
    ),
    CommandCheck(
        "C3",
        "C3 RESPF",
        "RESPF → STAT → STOP",
        _app_check("RESPF", lambda client: client.start_respf()),
        _cleanup_stop,
    ),
    CommandCheck(
        "C4",
        "C4 Sesión TWR (2 placas)",
        "RESPF (placa B) + INITF (placa A) → SESSION_INFO_NTF",
        _c4_twr_session,
        _c4_cleanup,
        needs_second_board=True,
    ),
)
