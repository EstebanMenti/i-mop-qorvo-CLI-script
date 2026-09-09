"""Punto de entrada de la GUI de escritorio (``dwm-gui``, fase F9).

Configura su propio logging (en vez de reusar ``app/logging_setup.py``):
``gui`` no debe importar de ``app`` (regla de capas, ver ``gui/__init__.py``)
— es una capa de presentación hermana, no una extensión de la CLI Typer.
"""

from __future__ import annotations

import logging
import sys
import threading
import types
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication

from dwm3001c_cli.gui.main_window import MainWindow

logger = logging.getLogger(__name__)


def _configure_logging(log_dir: Path = Path("logs")) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"dwm-gui-{datetime.now():%Y%m%d-%H%M%S}.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )


def _install_crash_logging() -> None:
    """Loguea cualquier excepción no atrapada antes de que termine el proceso.

    [Investigación 2026-09-09, hardware real] La app se cerraba sola, sin
    ninguna traza — ni excepción de Python, ni volcado nativo capturable con
    ProcDump con monitoreo de excepciones activado (se confirmó contra
    hardware real, volcado real analizado con WinDbg: el proceso hacía un
    ``Py_Exit``/``ExitProcess`` limpio, un solo hilo restante, **sin ningún
    fallo de hardware ni excepción SEH involucrados** — descarta la teoría
    previa de un crash nativo WinRT tipo ``STATUS_STACK_BUFFER_OVERRUN``,
    que si deja ese tipo de rastro y no dejó ninguno acá). La hipótesis de
    que fuera una excepción de Python escapando de un slot Qt normal
    (conectado a una señal cross-thread) se probó de forma aislada y **no
    se confirmó**: en esta versión de PySide6, ese caso imprime el
    traceback y el proceso sigue vivo. La causa raíz puntual sigue sin
    confirmarse, pero el patrón (cierre limpio del intérprete, sin
    excepción SEH) es consistente con algún tipo de terminación disparada
    desde código Python, no con un fallo de hardware — instalar
    ``sys.excepthook``/``threading.excepthook`` no elimina la causa, pero
    convierte una futura muerte silenciosa en un traceback real en el log
    en vez de tener que reconstruir todo por volcado nativo de nuevo.
    """

    def log_and_reraise_default(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: types.TracebackType | None,
    ) -> None:
        logger.critical(
            "Excepción no atrapada (hilo principal)", exc_info=(exc_type, exc_value, exc_tb)
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def log_thread_exception(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is None:
            return
        logger.critical(
            "Excepción no atrapada en hilo %r",
            args.thread,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = log_and_reraise_default
    threading.excepthook = log_thread_exception


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
    _install_crash_logging()
    _allow_bleak_sta()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main_gui()
