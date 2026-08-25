"""Punto de entrada de la GUI de escritorio (``dwm-gui``, fase F9).

Configura su propio logging (en vez de reusar ``app/logging_setup.py``):
``gui`` no debe importar de ``app`` (regla de capas, ver ``gui/__init__.py``)
— es una capa de presentación hermana, no una extensión de la CLI Typer.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication

from dwm3001c_cli.gui.main_window import MainWindow


def _configure_logging(log_dir: Path = Path("logs")) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"dwm-gui-{datetime.now():%Y%m%d-%H%M%S}.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )


def _allow_bleak_sta() -> None:
    """Evita el crash nativo (sin traza de Python) al conectar por BLE.

    [Bug real, verificado 2026-08-25 contra hardware real] PySide6 inicializa
    el hilo principal como apartamento COM STA (lo necesita para integrarse
    con Windows nativo); el backend WinRT de ``bleak`` da por sentado MTA.
    Sin este ajuste, conectar el RESPONDER por Bluetooth desde la GUI
    crasheaba el proceso entero en cuanto Windows procesaba actividad de UI
    real (clics) en simultáneo con la conexión GATT — no reproducible
    llamando a los mismos métodos sin interacción real de mouse, lo que
    demoró el diagnóstico. Ver la sección "Windows" de
    https://bleak.readthedocs.io/en/latest/troubleshooting.html. ``bleak`` es
    una dependencia opcional (extra ``[ble]``, no ``[gui]``): sin ella
    instalada, no hay nada que ajustar.
    """
    try:
        from bleak.backends.winrt.util import allow_sta
    except ImportError:
        return
    allow_sta()


def main_gui() -> None:
    """Punto de entrada del script ``dwm-gui`` (ver ``pyproject.toml``)."""
    _configure_logging()
    _allow_bleak_sta()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main_gui()
