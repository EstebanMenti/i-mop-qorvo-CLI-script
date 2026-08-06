"""Configuración centralizada de logging.

La consola muestra nivel INFO (o DEBUG con ``verbose``); el archivo de log
registra siempre DEBUG, incluido el tráfico serie crudo TX/RX que emite la capa
de transporte. Un archivo nuevo por ejecución, con marca de tiempo en el nombre.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
CONSOLE_FORMAT = "%(levelname)s: %(message)s"

# Marca para reconocer (y reemplazar) los handlers propios si se llama dos veces.
_HANDLER_TAG = "dwm3001c_cli"


def setup_logging(verbose: bool, log_dir: Path) -> None:
    """Configura el logger raíz con salida a consola y a archivo.

    Args:
        verbose: si es ``True``, la consola muestra DEBUG; si no, INFO.
        log_dir: carpeta donde se crea ``dwm-<AAAAMMDD-HHMMSS>.log``
            (se crea si no existe; no se versiona).

    La función es idempotente: llamadas repetidas reemplazan los handlers
    propios en lugar de duplicarlos.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"dwm-{datetime.now():%Y%m%d-%H%M%S}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    for handler in [h for h in root.handlers if getattr(h, _HANDLER_TAG, False)]:
        root.removeHandler(handler)
        handler.close()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(logging.Formatter(CONSOLE_FORMAT))

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT))

    for handler in (console_handler, file_handler):
        setattr(handler, _HANDLER_TAG, True)
        root.addHandler(handler)

    logging.getLogger(__name__).debug("Logging inicializado; archivo: %s", log_file)
