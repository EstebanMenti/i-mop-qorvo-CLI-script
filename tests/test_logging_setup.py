"""Tests de la configuración de logging (fase F0)."""

import logging
from pathlib import Path

from dwm3001c_cli.app.logging_setup import setup_logging


def _own_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if getattr(h, "dwm3001c_cli", False)]


def _teardown() -> None:
    root = logging.getLogger()
    for handler in _own_handlers():
        root.removeHandler(handler)
        handler.close()


def test_creates_log_dir_and_file(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    try:
        setup_logging(verbose=False, log_dir=log_dir)
        logging.getLogger("test").debug("mensaje de prueba")

        files = list(log_dir.glob("dwm-*.log"))
        assert len(files) == 1
        assert "mensaje de prueba" in files[0].read_text(encoding="utf-8")
    finally:
        _teardown()


def test_repeated_calls_do_not_duplicate_handlers(tmp_path: Path) -> None:
    try:
        setup_logging(verbose=False, log_dir=tmp_path)
        setup_logging(verbose=True, log_dir=tmp_path)

        assert len(_own_handlers()) == 2  # consola + archivo, sin duplicados
    finally:
        _teardown()
