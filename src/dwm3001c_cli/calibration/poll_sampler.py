"""Muestreo de una sesión TWR **por polling de comandos** (puente BLE).

El puente nRF52840 solo reenvía al PC la *respuesta a un comando*; las
notificaciones ``SESSION_INFO_NTF`` que el Qorvo emite espontáneamente durante
el ranging **no** llegan por sí solas, pero las que se acumularon sí llegan
junto con la respuesta del próximo comando (verificado contra hardware real,
``docs/verificacion-comandos-responder-ble.md`` §3.4 — ``STOP`` con backlog).
Además, el firmware CLI **no tiene ningún comando que consulte la distancia**
(``docs/referencia-comandos-fw110.md`` §5.1): la única vía de lectura es la
notificación, y por el puente BLE la única forma de provocar su envío es enviar
un comando.

Este sampler arranca la sesión igual que :func:`~dwm3001c_cli.calibration.sampler.collect_samples`
(primero ``RESPF``, después ``INITF``) pero obtiene las mediciones enviando
periódicamente un comando *anytime* benigno (``THREAD`` por defecto) al
**initiator** y parseando las ``SESSION_INFO_NTF`` que llegan intercaladas en
la respuesta. Es el sampler que usa la calibración con **ambas placas por
Bluetooth** (GUI ``dwm-gui``, pestaña "Calibración BLE").

Mismos umbrales de calidad (≥ 50 % de SUCCESS) y mismas estadísticas que
``collect_samples`` — la validación final es compartida
(:func:`~dwm3001c_cli.calibration.sampler.build_stats_or_fail`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable

from dwm3001c_cli.calibration.sampler import SessionParams, build_stats_or_fail
from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.core.errors import CalibrationError, CommandTimeoutError
from dwm3001c_cli.core.models import Measurement, RangingStats
from dwm3001c_cli.core.parsers import parse_session_info

logger = logging.getLogger(__name__)

# Comando *anytime* benigno usado para provocar el drenaje de notificaciones:
# no requiere modo NONE (funciona con la sesión corriendo) y su salida es corta.
DEFAULT_POLL_CMD = "THREAD"

# Si el puente pierde la notificación con el ``ok`` de la respuesta (las BLE no
# tienen ACK), ``send_command`` vence por timeout. Un timeout aislado se tolera;
# varios seguidos indican un enlace roto de verdad.
MAX_CONSECUTIVE_TIMEOUTS = 10


def extract_measurements(lines: Iterable[str], source: str) -> list[Measurement]:
    """Extrae las :class:`Measurement` de las líneas de una respuesta.

    Reensambla las ``SESSION_INFO_NTF`` que llegan **partidas en dos líneas**
    (fw 1.1.0: la continuación arranca con un ``\\r`` residual) acumulando hasta
    balancear las llaves ``{}``, igual que ``DwmCliClient.read_notifications``.
    Las líneas que no son notificaciones (salida propia del comando de polling,
    ``ok`` final) se ignoran.
    """
    measurements: list[Measurement] = []
    fragment: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("SESSION_INFO_NTF"):
            fragment = [stripped]
        elif fragment:
            fragment.append(stripped)
        else:
            continue
        joined = " ".join(fragment)
        if joined.count("{") > joined.count("}"):
            if len(fragment) > 8:
                logger.warning("Notificación inconclusa descartada en %s: %r", source, joined)
                fragment = []
            continue
        fragment = []
        try:
            measurements.append(parse_session_info(joined))
        except ValueError:
            logger.warning("Notificación no parseable en %s: %r", source, joined)
    return measurements


def collect_samples_polled(
    initiator: DwmCliClient,
    responder: DwmCliClient,
    *,
    n_samples: int,
    session_params: SessionParams | None = None,
    timeout_s: float | None = None,
    poll_cmd: str = DEFAULT_POLL_CMD,
    poll_interval_s: float = 0.25,
    max_consecutive_timeouts: int = MAX_CONSECUTIVE_TIMEOUTS,
    on_measurement: Callable[[Measurement], None] | None = None,
) -> RangingStats:
    """Corre una sesión TWR y junta ``n_samples`` mediciones SUCCESS **por polling**.

    Idéntico contrato que
    :func:`~dwm3001c_cli.calibration.sampler.collect_samples` (mismos umbrales
    de calidad, mismas placas detenidas al salir, incluso ante error), con la
    diferencia de que las mediciones se obtienen consultando al initiator con
    ``poll_cmd`` en vez de leer notificaciones espontáneas.

    Args:
        timeout_s: tiempo máximo total; default ``n_samples * BLOCK * 3``.
        poll_interval_s: pausa entre comandos de polling cuando un poll no trajo
            mediciones nuevas (da tiempo a que se acumulen rondas nuevas).
        max_consecutive_timeouts: timeouts consecutivos del comando de polling
            tolerados antes de abortar (una notificación BLE perdida es normal;
            diez seguidos, no).
        on_measurement: callback invocado con cada medición recibida (incluidas
            las ``RX_TIMEOUT``); para mostrar la distancia en vivo.

    Raises:
        CalibrationError: si no se juntan muestras suficientes, la tasa de éxito
            es menor al 50 %, o el comando de polling vence por timeout
            ``max_consecutive_timeouts`` veces seguidas.
    """
    params = session_params or SessionParams()
    limit = timeout_s if timeout_s is not None else n_samples * params.block_ms * 3 / 1000
    successes: list[int] = []
    received = 0
    consecutive_timeouts = 0

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
            try:
                lines = initiator.send_command(poll_cmd)
            except CommandTimeoutError:
                consecutive_timeouts += 1
                logger.warning(
                    "Polling %r sin respuesta en %s (intento %d/%d)",
                    poll_cmd,
                    initiator.name,
                    consecutive_timeouts,
                    max_consecutive_timeouts,
                )
                if consecutive_timeouts >= max_consecutive_timeouts:
                    raise CalibrationError(
                        f"{initiator.name}: el comando de polling {poll_cmd!r} no tuvo "
                        f"respuesta {consecutive_timeouts} veces seguidas — "
                        "revisar el enlace BLE con el puente."
                    ) from None
                time.sleep(poll_interval_s)
                continue
            consecutive_timeouts = 0
            measurements = extract_measurements(lines, initiator.name)
            received += len(measurements)
            for measurement in measurements:
                if on_measurement is not None:
                    on_measurement(measurement)
                if measurement.status == "SUCCESS" and measurement.distance_cm is not None:
                    successes.append(measurement.distance_cm)
            if len(measurements) == 0:
                # Sin notificaciones acumuladas: esperar a que el ranging genere
                # rondas nuevas (una por BLOCK ms) antes del próximo poll.
                time.sleep(poll_interval_s)
    finally:
        initiator.ensure_mode_none()
        responder.ensure_mode_none()

    stats = build_stats_or_fail(successes, received, n_samples, limit)
    logger.info(
        "Muestreo por polling: %d/%d SUCCESS, media %.1f cm, desvío %.1f cm",
        stats.n_success,
        stats.n_requested,
        stats.mean_cm,
        stats.std_cm,
    )
    return stats
