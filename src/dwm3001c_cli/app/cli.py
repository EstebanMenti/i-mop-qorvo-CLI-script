"""Aplicación de consola ``dwm`` (plan §7).

Cablea las capas inferiores (transport/core/validation/calibration) a una CLI
Typer. Es la única capa con salida a pantalla y entrada interactiva.

Reglas transversales aplicadas en todos los subcomandos con opciones:

- ``--verbose``/``--log-dir`` (definidas una sola vez, en el callback de la
  app) inicializan el logging antes de correr cualquier comando.
- ``--config archivo.yaml``: precedencia CLI > YAML > default (config.py). El
  YAML solo admite las mismas claves que las opciones del subcommand.
- Toda ``Dwm3001cError`` se traduce a un mensaje claro (el traceback va al
  log) y ``exit code`` 1; errores de uso de Typer/Click devuelven 2.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from dwm3001c_cli.app.config import load_yaml_config, resolve_option
from dwm3001c_cli.app.logging_setup import setup_logging
from dwm3001c_cli.calibration.autocal import AutocalConfig, CalibrationIteration, autocalibrate
from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.core.errors import Dwm3001cError
from dwm3001c_cli.transport.discovery import find_boards
from dwm3001c_cli.transport.serial_link import SerialLink
from dwm3001c_cli.validation.report import render_console, status_of, write_reports
from dwm3001c_cli.validation.runner import run_validation

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="dwm",
    help="Validación de comandos CLI y calibración de antenna delay del DWM3001C.",
    no_args_is_help=True,
)
console = Console()


@app.callback()
def _main(
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Muestra DEBUG también en consola.")
    ] = False,
    log_dir: Annotated[Path, typer.Option("--log-dir", help="Carpeta de archivos de log.")] = Path(
        "logs"
    ),
) -> None:
    setup_logging(verbose=verbose, log_dir=log_dir)


@contextmanager
def _error_boundary() -> Iterator[None]:
    """Traduce ``Dwm3001cError`` a un mensaje claro; el resto sube sin tocar."""
    try:
        yield
    except Dwm3001cError as exc:
        logger.exception("Comando abortado por un error de dominio")
        console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=1) from exc


# --------------------------------------------------------------------- ports


@app.command()
def ports() -> None:
    """Lista las placas DWM3001CDK detectadas en los puertos COM del sistema."""
    with _error_boundary():
        boards = find_boards()
    if not boards:
        console.print("[yellow]No se detectó ninguna placa DWM3001CDK conectada.[/]")
        raise typer.Exit(code=1)

    table = Table(title="Placas DWM3001CDK detectadas")
    table.add_column("Puerto")
    table.add_column("Descripción")
    table.add_column("N° de serie")
    table.add_column("Interfaz")
    for board in boards:
        table.add_row(
            board.port, board.description, board.serial_number or "-", board.interface_hint
        )
    console.print(table)


# ---------------------------------------------------------------------- info


@app.command()
def info(
    port: Annotated[
        str | None, typer.Option("--port", help="Puerto de la placa, p. ej. COM7.")
    ] = None,
    config: Annotated[Path | None, typer.Option("--config", help="YAML con claves: port.")] = None,
) -> None:
    """Muestra STAT, DECAID, GETOTP y LISTCAL de una placa (todo solo lectura)."""
    with _error_boundary():
        yaml_cfg = load_yaml_config(config, allowed_keys={"port"})
        resolved_port = resolve_option("port", port, yaml_cfg, None)
        if not resolved_port:
            console.print("[bold red]Error:[/] falta --port (o la clave 'port' en --config)")
            raise typer.Exit(code=2)

        with SerialLink(resolved_port) as link:
            client = DwmCliClient(link)
            client.ensure_mode_none()
            device = client.stat()
            chip = client.decaid()
            cal_keys = client.listcal()
            otp_lines = client.getotp()

    device_table = Table(title=f"STAT — {resolved_port}")
    device_table.add_column("Campo")
    device_table.add_column("Valor")
    for field, value in (
        ("Modo", device.mode),
        ("Dispositivo", device.device),
        ("App actual", device.current_app),
        ("Versión", device.version),
        ("Build", device.build),
        ("Apps", ", ".join(device.apps)),
        ("Driver", device.driver),
        ("UWB stack", device.uwb_stack),
    ):
        device_table.add_row(field, value)
    console.print(device_table)

    chip_table = Table(title="DECAID")
    chip_table.add_column("Campo")
    chip_table.add_column("Valor")
    for field, value in (
        ("Device ID", chip.device_id),
        ("Lot ID", chip.lot_id),
        ("Part ID", chip.part_id),
        ("SoC ID", chip.soc_id),
    ):
        chip_table.add_row(field, value)
    console.print(chip_table)

    cal_table = Table(title=f"LISTCAL — {len(cal_keys)} claves")
    cal_table.add_column("Clave")
    cal_table.add_column("Valor")
    for name in ("ant0.ch5.ant_delay", "ant0.ch9.ant_delay"):
        if name in cal_keys:
            cal_table.add_row(name, f"0x{cal_keys[name].value:X} ({cal_keys[name].value})")
    console.print(cal_table)

    console.print(
        f"[dim]GETOTP: {len(otp_lines)} líneas "
        "(usar --verbose y el log para el volcado completo)[/]"
    )
    logger.debug("GETOTP crudo:\n%s", "\n".join(otp_lines))


# ------------------------------------------------------------- ble-provision


@app.command(name="ble-provision")
def ble_provision(
    port: Annotated[
        str, typer.Option("--port", help="Puerto USB del Qorvo a habilitar para el puente BLE.")
    ],
    yes: Annotated[bool, typer.Option("--yes", help="Saltea la confirmación interactiva.")] = False,
) -> None:
    """Habilita, una única vez, la salida UART física del Qorvo (``UART 1`` + ``SAVE``).

    Paso previo obligatorio para usar el Qorvo como RESPONDER a través de un
    puente Bluetooth nRF52840 (rama ``hardware/ble-bridge-nrf52840``, ver
    ``docs/rama-hardware-ble.md``): de fábrica el Qorvo solo responde por USB,
    y el puente le habla por UART física.
    """
    with _error_boundary(), SerialLink(port) as link:
        client = DwmCliClient(link)
        client.ensure_mode_none()
        before = client.uart_status()
        console.print(f"[dim]UART actual en {port}:[/] {' '.join(before)}")

        if not yes:
            confirmed = typer.confirm(
                f"Se va a habilitar UART 1 y SAVE en {port} (paso permanente, "
                "solo se puede deshacer con RESTORE). ¿Continuar?"
            )
            if not confirmed:
                console.print("Provisioning cancelado por el usuario.")
                raise typer.Exit(code=1)

        client.enable_uart_output()
        client.save()
        after = client.uart_status()

    console.print(f"[bold green]UART habilitada y guardada en {port}:[/] {' '.join(after)}")


# ------------------------------------------------------------------ validate


@app.command()
def validate(
    port: Annotated[
        str | None, typer.Option("--port", help="Puerto de la placa principal.")
    ] = None,
    second_port: Annotated[
        str | None,
        typer.Option("--second-port", help="Puerto de la segunda placa (habilita el check C4)."),
    ] = None,
    report_dir: Annotated[
        Path | None,
        typer.Option("--report-dir", help="Carpeta de reportes (default: reports/)."),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="YAML con claves: port, second_port, report_dir."),
    ] = None,
) -> None:
    """Corre la suite de validación de comandos y escribe reportes JSON+Markdown."""
    with _error_boundary():
        yaml_cfg = load_yaml_config(config, allowed_keys={"port", "second_port", "report_dir"})
        resolved_port = resolve_option("port", port, yaml_cfg, None)
        resolved_second_port = resolve_option("second_port", second_port, yaml_cfg, None)
        resolved_report_dir = Path(resolve_option("report_dir", report_dir, yaml_cfg, "reports"))
        if not resolved_port:
            console.print("[bold red]Error:[/] falta --port (o la clave 'port' en --config)")
            raise typer.Exit(code=2)

        with ExitStack() as stack:
            link = stack.enter_context(SerialLink(resolved_port))
            client = DwmCliClient(link)
            second_client = None
            if resolved_second_port:
                second_link = stack.enter_context(SerialLink(resolved_second_port))
                second_client = DwmCliClient(second_link)

            results = run_validation(client, second_client=second_client)
            device = client.stat()

    render_console(results, console)
    json_path, md_path = write_reports(
        results, report_dir=resolved_report_dir, port=resolved_port, device=device
    )
    console.print(f"Reportes: [green]{json_path}[/], [green]{md_path}[/]")

    if any(status_of(result) == "FAIL" for result in results):
        raise typer.Exit(code=1)


# ----------------------------------------------------------------- calibrate


def _print_iteration(iteration: CalibrationIteration) -> None:
    correction = (
        f", corrección {iteration.correction_units:+d}"
        if iteration.correction_units is not None
        else ""
    )
    console.print(
        f"[cyan]Iteración {iteration.index}[/]: delay={iteration.delay}  "
        f"media={iteration.mean_cm:.1f} cm  desvío={iteration.std_cm:.1f} cm  "
        f"error={iteration.error_cm:+.1f} cm{correction}"
    )


@app.command()
def calibrate(
    initiator: Annotated[
        str | None, typer.Option("--initiator", help="Puerto de la placa de referencia (INITF).")
    ] = None,
    responder: Annotated[
        str | None, typer.Option("--responder", help="Puerto de la placa a calibrar (RESPF).")
    ] = None,
    distance_m: Annotated[
        float | None,
        typer.Option("--distance-m", help="Distancia real entre placas, en metros."),
    ] = None,
    samples: Annotated[
        int | None, typer.Option("--samples", help="Muestras por medición (default: 100).")
    ] = None,
    tolerance_cm: Annotated[
        float | None,
        typer.Option("--tolerance-cm", help="Tolerancia de convergencia en cm (default: 2.0)."),
    ] = None,
    max_iterations: Annotated[
        int | None,
        typer.Option("--max-iterations", help="Iteraciones máximas de corrección (default: 6)."),
    ] = None,
    channel: Annotated[
        int | None, typer.Option("--channel", help="Canal FiRa a calibrar (default: 9).")
    ] = None,
    no_save: Annotated[
        bool, typer.Option("--no-save", help="No ejecutar SAVE al converger.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", help="Saltea la confirmación interactiva de escritura.")
    ] = False,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            help="YAML con claves: initiator, responder, distance_m, samples, "
            "tolerance_cm, max_iterations, channel. "
            "--no-save y --yes son solo de línea de comandos (no configurables por YAML).",
        ),
    ] = None,
) -> None:
    """Calibra automáticamente el antenna delay del responder contra el initiator."""
    with _error_boundary():
        # no_save y yes quedan fuera de las claves de YAML a propósito: son
        # toggles de seguridad/comportamiento que deben ser explícitos en la
        # línea de comandos, no heredables silenciosamente de un archivo.
        yaml_cfg = load_yaml_config(
            config,
            allowed_keys={
                "initiator",
                "responder",
                "distance_m",
                "samples",
                "tolerance_cm",
                "max_iterations",
                "channel",
            },
        )
        resolved_initiator = resolve_option("initiator", initiator, yaml_cfg, None)
        resolved_responder = resolve_option("responder", responder, yaml_cfg, None)
        resolved_distance_m = resolve_option("distance_m", distance_m, yaml_cfg, None)
        missing = [
            name
            for name, value in (
                ("--initiator", resolved_initiator),
                ("--responder", resolved_responder),
                ("--distance-m", resolved_distance_m),
            )
            if not value
        ]
        if missing:
            console.print(f"[bold red]Error:[/] faltan opciones obligatorias: {', '.join(missing)}")
            raise typer.Exit(code=2)

        config_obj = AutocalConfig(
            n_samples=resolve_option("samples", samples, yaml_cfg, 100),
            tolerance_cm=resolve_option("tolerance_cm", tolerance_cm, yaml_cfg, 2.0),
            max_iterations=resolve_option("max_iterations", max_iterations, yaml_cfg, 6),
            channel=resolve_option("channel", channel, yaml_cfg, 9),
            do_save=not no_save,
        )

        with (
            SerialLink(resolved_initiator) as initiator_link,
            SerialLink(resolved_responder) as responder_link,
        ):
            initiator_client = DwmCliClient(initiator_link)
            responder_client = DwmCliClient(responder_link)
            responder_client.ensure_mode_none()
            initiator_client.ensure_mode_none()

            current = responder_client.calkey_read(config_obj.key)
            if not yes:
                confirmed = typer.confirm(
                    f"Se va a calibrar {config_obj.key} en {resolved_responder} "
                    f"(valor actual: {current.value}). ¿Continuar?"
                )
                if not confirmed:
                    console.print("Calibración cancelada por el usuario.")
                    raise typer.Exit(code=1)

            report = autocalibrate(
                responder_client,
                initiator_client,
                real_distance_m=resolved_distance_m,
                config=config_obj,
                on_iteration=_print_iteration,
            )

    console.print(
        f"[bold green]Calibración convergida:[/] {report.key} "
        f"{report.initial_delay} → {report.final_delay} "
        f"({'guardado en NVM' if report.saved else 'sin guardar'})"
    )


# ------------------------------------------------------------------ terminal


@app.command()
def terminal(
    port: Annotated[str, typer.Option("--port", help="Puerto de la placa.")],
) -> None:
    """Terminal interactivo crudo contra la consola CLI del firmware."""
    with _error_boundary(), SerialLink(port) as link:
        console.print(f"Terminal interactivo en {port}. Ctrl+C para salir.")
        stop_event = threading.Event()

        def _write_loop() -> None:
            try:
                while not stop_event.is_set():
                    line = input()
                    link.write_line(line)
            except EOFError:
                pass

        writer = threading.Thread(target=_write_loop, daemon=True)
        writer.start()
        try:
            while True:
                line = link.read_line(timeout_s=0.2)
                if line is not None:
                    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    console.print(f"[dim]{timestamp}[/] {line}")
        except KeyboardInterrupt:
            stop_event.set()
            console.print("\nCerrando terminal.")


def main() -> None:
    """Punto de entrada del script ``dwm`` (ver ``pyproject.toml``)."""
    app()


if __name__ == "__main__":
    main()
