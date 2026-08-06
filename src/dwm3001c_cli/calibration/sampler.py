"""Muestreo de una sesión TWR entre dos placas (plan §6.1).

Orquesta ``RESPF`` + ``INITF``, recolecta mediciones del initiator y calcula
estadísticas. Detiene ambas placas siempre, incluso ante error.
"""

from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass

from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.core.errors import CalibrationError
from dwm3001c_cli.core.models import RangingStats

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionParams:
    """Parámetros FiRa de la sesión TWR (defaults del firmware, tabla 7.6).

    Se envía **siempre el juego completo** de parámetros en ``INITF``/``RESPF``:
    cualquier parámetro provisto resetea los demás a default (guía §2.4), así
    que enviar todos evita estados a medias.
    """

    chan: int = 9
    prfset: str = "BPRF4"
    pcode: int = 10
    slot: int = 2400
    block_ms: int = 200
    round_slots: int = 25
    rru: str = "DSTWR"
    session_id: int = 42
    vupper: str = "01:02:03:04:05:06:07:08"

    def _common_kwargs(self) -> dict[str, object]:
        return {
            "chan": self.chan,
            "prfset": self.prfset,
            "pcode": self.pcode,
            "slot": self.slot,
            "block": self.block_ms,
            "round": self.round_slots,
            "rru": self.rru,
            "id": self.session_id,
            "vupper": self.vupper,
        }

    def initiator_kwargs(self) -> dict[str, object]:
        """Opciones completas para ``INITF`` (ADDR=0, PADDR=1 — defaults del rol)."""
        return {**self._common_kwargs(), "addr": 0, "paddr": 1}

    def responder_kwargs(self) -> dict[str, object]:
        """Opciones completas para ``RESPF`` (ADDR=1, PADDR=0 — defaults del rol)."""
        return {**self._common_kwargs(), "addr": 1, "paddr": 0}


def collect_samples(
    initiator: DwmCliClient,
    responder: DwmCliClient,
    *,
    n_samples: int,
    session_params: SessionParams | None = None,
    timeout_s: float | None = None,
) -> RangingStats:
    """Corre una sesión TWR y junta ``n_samples`` mediciones SUCCESS.

    Orden de arranque: primero el responder, después el initiator (guía §4.2).
    Las mediciones se leen del initiator. Al terminar (o ante cualquier error)
    ambas placas vuelven a modo NONE.

    Args:
        timeout_s: tiempo máximo total; default ``n_samples * BLOCK * 3``.

    Raises:
        CalibrationError: si no se juntan muestras suficientes o la tasa de
            éxito es menor al 50 % (enlace malo: no tiene sentido calibrar).
    """
    params = session_params or SessionParams()
    limit = timeout_s if timeout_s is not None else n_samples * params.block_ms * 3 / 1000
    successes: list[int] = []
    received = 0

    try:
        responder.ensure_mode_none()
        initiator.ensure_mode_none()
        responder.start_respf(**params.responder_kwargs())
        initiator.start_initf(**params.initiator_kwargs())

        deadline = time.monotonic() + limit
        while len(successes) < n_samples:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            window = min(remaining, params.block_ms * 3 / 1000)
            for measurement in initiator.read_notifications(duration_s=window, max_count=1):
                received += 1
                if measurement.status == "SUCCESS" and measurement.distance_cm is not None:
                    successes.append(measurement.distance_cm)
    finally:
        initiator.ensure_mode_none()
        responder.ensure_mode_none()

    if not successes:
        raise CalibrationError(
            f"Sin mediciones SUCCESS en {limit:.0f} s "
            f"({received} notificaciones recibidas). Revisar el enlace entre placas."
        )
    success_rate = len(successes) / received
    if len(successes) < n_samples / 2 or success_rate < 0.5:
        raise CalibrationError(
            f"Enlace pobre: {len(successes)}/{n_samples} muestras SUCCESS "
            f"(tasa {success_rate:.0%} sobre {received} notificaciones). "
            "Revisar montaje antes de calibrar."
        )

    stats = RangingStats(
        n_requested=n_samples,
        n_received=received,
        n_success=len(successes),
        mean_cm=statistics.fmean(successes),
        std_cm=statistics.pstdev(successes),
        min_cm=min(successes),
        max_cm=max(successes),
    )
    logger.info(
        "Muestreo: %d/%d SUCCESS, media %.1f cm, desvío %.1f cm",
        stats.n_success,
        stats.n_requested,
        stats.mean_cm,
        stats.std_cm,
    )
    return stats
