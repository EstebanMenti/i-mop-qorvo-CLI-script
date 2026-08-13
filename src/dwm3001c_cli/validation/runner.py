"""Ejecutor de la suite de validación de comandos (plan §5.2).

Recorre :data:`~dwm3001c_cli.validation.spec.ALL_CHECKS` en orden. Un check que
falla **no aborta la suite**: se registra como FAIL y su limpieza se ejecuta
igual. Los checks que requieren segunda placa se marcan SKIP si no la hay.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Collection

from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.core.models import ValidationResult
from dwm3001c_cli.validation.spec import ALL_CHECKS, CheckContext, CheckFailedError

logger = logging.getLogger(__name__)


def run_validation(
    client: DwmCliClient,
    *,
    second_client: DwmCliClient | None = None,
    settle_delay_s: float = 3.0,
    ranging_window_s: float = 10.0,
    check_ids: Collection[str] | None = None,
    on_result: Callable[[ValidationResult], None] | None = None,
) -> list[ValidationResult]:
    """Corre la suite (o un subconjunto por ``check_ids``) y devuelve los resultados.

    Args:
        client: placa principal (obligatoria).
        second_client: segunda placa; habilita el check C4 de sesión TWR.
        settle_delay_s: espera tras arrancar una aplicación antes de verificarla.
        ranging_window_s: ventana de escucha de notificaciones en C4.
        check_ids: si se indica, solo se ejecutan esos IDs (p. ej. ``{"A3", "C4"}``).
        on_result: si se indica, se llama con cada :class:`ValidationResult` a
            medida que se completa (incluidos los SKIP) — para mostrar
            progreso en vivo en una interfaz gráfica (rama
            ``hardware/ble-bridge-nrf52840``), sin acoplar este módulo a Rich
            ni a Qt.
    """
    context = CheckContext(
        client=client,
        second_client=second_client,
        settle_delay_s=settle_delay_s,
        ranging_window_s=ranging_window_s,
    )
    client.ensure_mode_none()

    results: list[ValidationResult] = []
    for check in ALL_CHECKS:
        if check_ids is not None and check.check_id not in check_ids:
            continue
        if check.needs_second_board and second_client is None:
            logger.info("%s: SKIP (requiere segunda placa)", check.check_id)
            result = ValidationResult(
                command=check.command,
                sent=check.sent,
                passed=True,
                detail="SKIP: requiere una segunda placa conectada",
                response_lines=(),
                duration_s=0.0,
            )
            results.append(result)
            if on_result is not None:
                on_result(result)
            continue

        logger.info("%s: ejecutando %s", check.check_id, check.sent)
        start = time.monotonic()
        passed = False
        detail = ""
        lines: list[str] = []
        try:
            detail, lines = check.run(context)
            passed = True
        except CheckFailedError as failure:
            detail = failure.detail
            lines = failure.response_lines
            logger.warning("%s: FAIL — %s", check.check_id, detail)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            logger.exception("%s: error inesperado", check.check_id)
        finally:
            if check.cleanup is not None:
                try:
                    check.cleanup(context)
                except Exception:
                    logger.exception("%s: la limpieza falló", check.check_id)

        result = ValidationResult(
            command=check.command,
            sent=check.sent,
            passed=passed,
            detail=detail,
            response_lines=tuple(lines),
            duration_s=time.monotonic() - start,
        )
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results
