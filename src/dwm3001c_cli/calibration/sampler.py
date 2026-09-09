"""Muestreo de una sesión TWR entre dos placas (plan §6.1).

Orquesta ``RESPF`` + ``INITF``, recolecta mediciones del initiator y calcula
estadísticas. Detiene ambas placas siempre, incluso ante error.

[Historial BLE, UWB-Node-6/-8 — corregido 2026-09-09] Este sampler (lectura
pasiva vía ``DwmCliClient.read_notifications``) funciona con el initiator
detrás del puente BLE nRF52840, pero el entendimiento de **por qué**
cambió dos veces:

1. [2026-09-08] Primera prueba (corta, 30 muestras): pareció que el puente
   reenviaba ``SESSION_INFO_NTF`` espontáneamente por NUS TX sin necesidad
   de comandos — 30/30 recibidas en orden.
2. [2026-09-09] Con muestreos más largos (100 muestras) eso resultó
   **incompleto**: el canal de comandos (``qorvo <cmd>``, shell sobre NUS)
   es petición/respuesta con una ventana acotada por el firmware puente
   (silencio 400ms / timeout duro 8000ms) y, al vencer, suspendía el UART
   hacia el Qorvo incondicionalmente. Como ``SESSION_INFO_NTF`` llega cada
   ``BLOCK`` ms sin pausa durante el ranging, el silencio nunca se cumplía:
   la ventana corría siempre hasta los 8000ms, volcaba una única ráfaga
   (``8000 / BLOCK`` notificaciones — con ``BLOCK=200`` eso son exactamente
   ~40) y todo lo que el Qorvo transmitía después se perdía hasta el
   próximo comando. Confirmado contra hardware real: ~40-44 SUCCESS y
   silencio total el resto de la ventana, sin ninguna desconexión BLE de
   por medio.
3. [2026-09-09] Corregido en el firmware del puente (``I-mop-nrf52840-fw``):
   nuevo servicio GATT dedicado, solo-Notify, de streaming continuo,
   activado con ``qorvo stream on`` (ver ``STREAM_SERVICE_UUID`` en
   ``transport/ble_link.py``) — deja el UART abierto indefinidamente y
   reenvía todo por una característica separada del canal de comandos.
   ``BleTransport.open()`` lo activa automáticamente. Confirmado contra
   hardware real: 100/100 SUCCESS en flujo continuo (~200ms entre
   muestras, sin ráfagas) en una sola sesión de 100 muestras.

Para BLE conviene igual pasar ``min_samples`` más bajo y ``timeout_s`` más
holgado (reconexiones ~7-8s de inactividad si no hay streaming activo, RF
más ruidoso que en banco USB-USB).
"""

from __future__ import annotations

import logging
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.core.errors import CalibrationError
from dwm3001c_cli.core.models import Measurement, RangingStats

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


def cleanup_both(initiator: DwmCliClient, responder: DwmCliClient) -> list[Exception]:
    """Intenta ``ensure_mode_none()`` en ambas placas, sin que el fallo de una
    impida intentarlo en la otra.

    [Bug real, 2026-09-08, hardware real] Un ``finally`` que encadena
    ``initiator.ensure_mode_none(); responder.ensure_mode_none()`` deja al
    responder sin limpiar si la del initiator revienta (p. ej. un enlace BLE
    inestable) — confirmado contra hardware real: el responder quedó
    trabado en RESPF, generando ranging sin fin, y la sesión siguiente
    arrancó con datos ya corruptos. Compartido por :func:`collect_samples` y
    ``calibration/poll_sampler.py``.
    """
    errors: list[Exception] = []
    for client in (initiator, responder):
        try:
            client.ensure_mode_none()
        except Exception as exc:  # nunca debe evitar limpiar el otro cliente
            logger.warning("%s: fallo al volver a NONE al limpiar", client.name, exc_info=True)
            errors.append(exc)
    return errors


def collect_samples(
    initiator: DwmCliClient,
    responder: DwmCliClient,
    *,
    n_samples: int,
    session_params: SessionParams | None = None,
    timeout_s: float | None = None,
    min_samples: int | None = None,
    on_measurement: Callable[[Measurement], None] | None = None,
) -> RangingStats:
    """Corre una sesión TWR y junta ``n_samples`` mediciones SUCCESS.

    Orden de arranque: primero el responder, después el initiator (guía §4.2).
    Las mediciones se leen del initiator. Al terminar (o ante cualquier error)
    ambas placas vuelven a modo NONE (best-effort en ambas, ver :func:`cleanup_both`).

    Args:
        timeout_s: tiempo máximo total; default ``n_samples * BLOCK * 3``.
        min_samples: ver :func:`build_stats_or_fail` (default: mitad de
            ``n_samples``). Pasar un piso menor para enlaces más lentos o
            ruidosos (p. ej. el initiator detrás de un puente BLE) que no
            llegan a ``n_samples/2`` en el tiempo disponible pero sí entregan
            una muestra parcial de buena calidad.
        on_measurement: callback opcional invocado con cada :class:`Measurement`
            recibida (incluidas las fallidas); para mostrar la distancia en
            vivo.

    [Descartado 2026-09-09, hardware real] Esta función tuvo un parámetro
    ``keepalive_interval_s`` que mandaba ``STAT`` periódico al ``responder``
    para evitar que su enlace BLE quedara inactivo (portado de
    ``i-mop-tools-measure``, necesario cuando las notificaciones viajaban por
    el canal de comandos NUS TX). Con el streaming BLE dedicado (ver
    ``transport/ble_link.py``, ``STREAM_SERVICE_UUID``) dejó de hacer falta
    — el propio tráfico de streaming mantiene viva la conexión — y además
    resultó **contraproducente**: mandar un comando (``STAT``) a una placa
    que está streameando activamente reabre la ventana de 8s del canal de
    comandos para la respuesta de *ese* comando, contaminándola con el
    backlog de notificaciones en curso (confirmado contra hardware real: un
    ``STAT`` de keepalive cortó una sesión a los ~44s con
    ``ValueError: Salida de STAT sin bloque JSxxxx``). Se eliminó.

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
                if on_measurement is not None:
                    on_measurement(measurement)
                if measurement.status == "SUCCESS" and measurement.distance_cm is not None:
                    successes.append(measurement.distance_cm)
    except BaseException:
        cleanup_both(initiator, responder)
        raise
    else:
        cleanup_errors = cleanup_both(initiator, responder)
        if cleanup_errors:
            raise cleanup_errors[0]

    stats = build_stats_or_fail(successes, received, n_samples, limit, min_samples=min_samples)
    logger.info(
        "Muestreo: %d/%d SUCCESS, media %.1f cm, desvío %.1f cm",
        stats.n_success,
        stats.n_requested,
        stats.mean_cm,
        stats.std_cm,
    )
    return stats


def build_stats_or_fail(
    successes: Sequence[int],
    received: int,
    n_samples: int,
    limit: float,
    *,
    min_samples: int | None = None,
    min_rate_pct: float = 50.0,
) -> RangingStats:
    """Valida la calidad del enlace y construye las estadísticas del muestreo.

    Compartido por :func:`collect_samples` y el sampler por polling de
    ``poll_sampler.py``: mismos umbrales y mismos mensajes de error.

    Args:
        min_samples: mínimo absoluto de muestras SUCCESS para aceptar. Default:
            la mitad de ``n_samples``. El sampler por polling lo baja
            ([verificado 2026-09-08, hardware real] los puentes BLE pueden
            estrangular la entrega de notificaciones a mitad de sesión — en un
            banco real se venció la ventana con 33 SUCCESS y tasa 94%, datos
            perfectamente útiles para calibrar) a cambio de exigir un piso
            absoluto de muestras.
        min_rate_pct: tasa mínima de SUCCESS sobre las notificaciones recibidas.

    Raises:
        CalibrationError: sin muestras SUCCESS, o menos de ``min_samples``
            SUCCESS, o tasa por debajo de ``min_rate_pct``
            (enlace malo: no tiene sentido calibrar).
    """
    required = min_samples if min_samples is not None else n_samples / 2
    if not successes:
        raise CalibrationError(
            f"Sin mediciones SUCCESS en {limit:.0f} s "
            f"({received} notificaciones recibidas). Revisar el enlace entre placas."
        )
    success_rate = len(successes) / received
    if len(successes) < required or success_rate < min_rate_pct / 100:
        raise CalibrationError(
            f"Enlace pobre: {len(successes)}/{n_samples} muestras SUCCESS "
            f"(tasa {success_rate:.0%} sobre {received} notificaciones). "
            "Revisar montaje antes de calibrar."
        )
    return RangingStats(
        n_requested=n_samples,
        n_received=received,
        n_success=len(successes),
        mean_cm=statistics.fmean(successes),
        std_cm=statistics.pstdev(successes),
        min_cm=min(successes),
        max_cm=max(successes),
    )
