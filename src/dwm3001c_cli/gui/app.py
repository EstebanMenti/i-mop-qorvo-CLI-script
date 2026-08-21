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


def main_gui() -> None:
    """Punto de entrada del script ``dwm-gui`` (ver ``pyproject.toml``)."""
    _configure_logging()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main_gui()
