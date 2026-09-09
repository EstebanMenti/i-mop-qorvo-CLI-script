"""Workers ``QObject`` para mover E/S bloqueante (serie o BLE) fuera del hilo
de UI (plan F9): se mueven a un ``QThread`` con ``moveToThread`` (no herencia
de ``QThread``, no ``QThreadPool``), porque necesitan vida larga con señales
de progreso continuas. Ninguno importa Qt widgets — solo ``QtCore``.
"""

from __future__ import annotations

import logging
import statistics
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from dwm3001c_cli.calibration.autocal import (
    AutocalConfig,
    CalibrationIteration,
    CalibrationReport,
    autocalibrate,
)
from dwm3001c_cli.calibration.sampler import SessionParams, cleanup_both, collect_samples
from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.core.models import Measurement, RangingStats, ValidationResult
from dwm3001c_cli.transport.discovery import BoardPort, find_boards
from dwm3001c_cli.transport.serial_link import Transport
from dwm3001c_cli.validation.runner import run_validation

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # Importado en forma diferida en tiempo de ejecución (ver ScanWorker.run):
    # el extra [ble] (bleak) no es una dependencia dura de la GUI, solo del
    # escaneo/conexión BLE. Acá solo hace falta para el chequeo de tipos.
    from dwm3001c_cli.transport.ble_discovery import BleBoardInfo
    from dwm3001c_cli.transport.ble_link import BleTransport


class ScanWorker(QObject):
    """Escanea placas USB y, si ``bleak`` está instalado, puentes BLE."""

    finished = Signal(list, list)  # list[BoardPort], list[BleBoardInfo]
    failed = Signal(str)

    @Slot()
    def run(self) -> None:
        try:
            usb_boards = find_boards()
        except Exception as exc:  # ver nota en ConnectWorker.run()
            self.failed.emit(str(exc))
            return
        ble_boards: list[BleBoardInfo] = []
        try:
            from dwm3001c_cli.transport.ble_discovery import find_ble_boards

            ble_boards = find_ble_boards()
        except ImportError:
            pass  # extra [ble] no instalado: la GUI sigue funcional solo con USB
        except Exception:
            pass  # sin adaptador BLE o escaneo fallido: no bloquea el resto
        self.finished.emit(usb_boards, ble_boards)


class ConnectWorker(QObject):
    """Conecta una placa (USB o BLE) sin bloquear el hilo de UI.

    ``factory`` hace todo el trabajo de E/S (abrir el transporte) y corre
    dentro del hilo del worker; devuelve el transporte ya abierto y el
    cliente construido sobre él, para que quien conectó las señales sea
    dueño de ambos objetos (necesita el transporte para poder cerrarlo).
    """

    connected = Signal(object, object)  # (Transport, DwmCliClient)
    failed = Signal(str)

    def __init__(self, factory: Callable[[], tuple[Transport, DwmCliClient]]) -> None:
        super().__init__()
        self._factory = factory

    @Slot()
    def run(self) -> None:
        try:
            transport, client = self._factory()
        except Exception as exc:
            # [Bug real, verificado 2026-08-25 contra hardware real] Antes
            # solo atrapaba Dwm3001cError: un OSError real de bleak/WinRT
            # (p. ej. "se cerró el objeto" cuando la sesión GATT se cae a
            # mitad del descubrimiento de servicios) se escapaba sin
            # atraparse, el hilo terminaba sin emitir connected ni failed, y
            # el botón "Conectar" quedaba trabado en "Conectando..." para
            # siempre, sin ningún mensaje de error. Un worker en background
            # nunca debe dejar escapar una excepción en silencio.
            self.failed.emit(str(exc))
            return
        self.connected.emit(transport, client)


class CallableWorker(QObject):
    """Corre una función bloqueante arbitraria sin bloquear el hilo de UI.

    Genérico a propósito (no sabe nada de BLE): lo usa
    ``ConnectionView`` para leer batería/firmware del puente bajo demanda
    (ver ``_fetch_ble_device_status`` ahí), fuera del momento de conectar.

    [Mitigación 2026-09-09, hardware real] La lectura de batería/firmware
    era automática apenas se conectaba cada nodo — confirmado contra
    hardware real (volcado nativo analizado con WinDbg, exit code real
    ``0xC0000409``/``STATUS_STACK_BUFFER_OVERRUN`` visible en el log de
    ProcDump) que eso aumentaba la frecuencia del crash ya documentado en
    ``docs/rama-hardware-ble.md`` §8 (condición de carrera Qt/WinRT): dos
    conexiones BLE por hilos de ``ConnectWorker`` separados podían terminar
    haciendo llamadas WinRT nativas superpuestas si el usuario conectaba el
    segundo nodo mientras el primero todavía estaba leyendo esas
    características. Pasarla a una acción manual (botón), disparada por el
    usuario un nodo a la vez, saca esa superposición concreta — no elimina
    el bug de fondo (río arriba, en PySide6/WinRT).
    """

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, factory: Callable[[], object]) -> None:
        super().__init__()
        self._factory = factory

    @Slot()
    def run(self) -> None:
        try:
            result = self._factory()
        except Exception as exc:  # nunca dejar escapar una excepción del worker
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)


class TerminalWorker(QObject):
    """Terminal manual: lee líneas del transporte activo con un ``QTimer``.

    ``run()`` arranca un ``QTimer`` y retorna enseguida — a propósito, *no*
    bloquea. Como es un slot directo de ``QThread.started`` (mismo hilo),
    corre *antes* de que ``QThread`` entre a su propio ``exec()``: un
    ``run()`` bloqueante (loop ``while`` propio) impediría que el loop de
    eventos del hilo arrancara, y con él, la entrega de cualquier señal en
    cola dirigida a este worker (p. ej. ``send``) — quedaría pendiente para
    siempre. Con el timer, ``run()`` retorna, ``exec()`` arranca de verdad, y
    tanto el polling (``timeout``) como ``send`` se procesan en el mismo loop
    de eventos, sin busy-loop ni ``processEvents()`` manual.
    """

    line_received = Signal(str)

    def __init__(self, transport: Transport) -> None:
        super().__init__()
        self._transport = transport
        self._timer: QTimer | None = None

    @Slot()
    def run(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(50)

    def _poll(self) -> None:
        try:
            line = self._transport.read_line(0.2)
        except Exception as exc:
            self.line_received.emit(f"[error] {exc}")
            if self._timer is not None:
                self._timer.stop()
            return
        if line is not None:
            self.line_received.emit(line)

    @Slot(str)
    def send(self, line: str) -> None:
        try:
            self._transport.write_line(line)
        except Exception as exc:
            self.line_received.emit(f"[error] {exc}")


class ValidationWorker(QObject):
    """Corre ``run_validation`` con progreso en vivo por check.

    Al terminar, también consulta ``STAT`` (igual que ``dwm validate`` en la
    CLI, ``app/cli.py``) para incluir la info del dispositivo en el reporte —
    se hace acá, en el hilo del worker, para no bloquear la UI con esa
    llamada extra de E/S.
    """

    check_completed = Signal(object)  # ValidationResult
    finished = Signal(list, object)  # (list[ValidationResult], DeviceInfo | None)
    failed = Signal(str)

    def __init__(self, client: DwmCliClient, *, second_client: DwmCliClient | None = None) -> None:
        super().__init__()
        self._client = client
        self._second_client = second_client

    @Slot()
    def run(self) -> None:
        try:
            results: list[ValidationResult] = run_validation(
                self._client,
                second_client=self._second_client,
                on_result=self.check_completed.emit,
            )
            device = self._client.stat()
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(results, device)


class BleScanWorker(QObject):
    """Escanea TODOS los dispositivos BLE al alcance (sin filtro de nombre).

    A diferencia de :class:`ScanWorker`, no presupone que los puentes se llaman
    "UWB Node": devuelve la lista completa para que la vista la muestre y el
    usuario filtre con el campo de texto.
    """

    finished = Signal(list)  # list[BleBoardInfo]
    failed = Signal(str)

    def __init__(self, timeout_s: float = 6.0) -> None:
        super().__init__()
        self._timeout_s = timeout_s

    @Slot()
    def run(self) -> None:
        try:
            from dwm3001c_cli.transport.ble_discovery import find_ble_devices

            devices = find_ble_devices(self._timeout_s)
        except Exception as exc:  # sin adaptador BLE, adaptador ocupado, etc.
            self.failed.emit(str(exc))
            return
        self.finished.emit(devices)


def _ble_sampler(
    initiator: DwmCliClient,
    responder: DwmCliClient,
    *,
    n_samples: int,
    session_params: SessionParams,
    on_measurement: Callable[[Measurement], None] | None = None,
) -> RangingStats:
    """Sampler para **ambas placas por Bluetooth**: ``collect_samples`` (lectura
    pasiva, la misma función que USB-USB) con presupuesto de tiempo más
    holgado y un piso de muestras más bajo.

    [Verificado 2026-09-09, hardware real, UWB-Node-6/-8, firmware del
    puente con streaming BLE dedicado — ver ``STREAM_SERVICE_UUID`` en
    ``transport/ble_link.py``] 100/100 SUCCESS en flujo continuo (~200ms
    entre muestras, sin ráfagas ni huecos) en una sola sesión de 100
    muestras. Antes del streaming, el canal de comandos (NUS TX) tenía una
    ventana acotada a 8s (bug del firmware puente, ya corregido) que
    limitaba cada sesión a ~40 muestras en una única ráfaga inicial y
    silencio después — de ahí venían el piso de muestras reducido
    (``_BLE_MIN_SAMPLES``) y el multiplicador de timeout más generoso
    (``_BLE_TIMEOUT_MULTIPLIER``) de acá abajo: ya no son estrictamente
    necesarios, pero se mantienen como margen de seguridad razonable (un
    enlace BLE puede seguir teniendo hipos puntuales) en vez de ajustarlos
    al límite sin más evidencia de campo.
    """
    block_ms = session_params.block_ms if session_params is not None else SessionParams().block_ms
    return collect_samples(
        initiator,
        responder,
        n_samples=n_samples,
        session_params=session_params,
        timeout_s=n_samples * block_ms * _BLE_TIMEOUT_MULTIPLIER / 1000,
        min_samples=min(_BLE_MIN_SAMPLES, n_samples),
        on_measurement=on_measurement,
    )


# Piso de muestras SUCCESS y multiplicador de timeout para el sampler BLE
# (ver docstring de _ble_sampler): margen de seguridad para hipos puntuales
# del enlace BLE, no un requisito estricto con el streaming dedicado activo.
_BLE_MIN_SAMPLES = 30
_BLE_TIMEOUT_MULTIPLIER = 5


@dataclass(frozen=True)
class BleDeviceStatus:
    """Estado adicional de un nodo BLE recién conectado — informativo, no
    crítico para la calibración (ver ``BleTransport.read_battery_level``/
    ``read_bridge_firmware_version``, best-effort, pueden ser ``None``).
    """

    role: str  # "initiator" o "responder"
    name: str
    address: str
    battery_pct: int | None
    firmware_version: str | None


def format_ble_device_status(battery_pct: int | None, firmware_version: str | None) -> str:
    """Texto corto para mostrar batería/firmware de un puente BLE en la UI.

    Compartido por ``ConnectionView`` y ``BleCalibrationView`` para que el
    mismo dato se vea igual en las dos pestañas. Ninguno de los dos campos es
    obligatorio (lecturas best-effort, ver ``BleTransport``).
    """
    parts = []
    if battery_pct is not None:
        parts.append(f"batería {battery_pct}%")
    if firmware_version is not None:
        parts.append(f"fw puente {firmware_version}")
    return ", ".join(parts) if parts else "sin datos de batería/firmware"


class BlePairCalibrationWorker(QObject):
    """Calibración con **ambas placas por Bluetooth** (puentes nRF52840).

    Abre los dos ``BleTransport``, corre ``autocalibrate`` con
    :func:`_ble_sampler` y cierra ambos transportes siempre, incluso ante
    error. Emite por señal cada medición recibida (para mostrar la distancia
    en vivo), cada iteración completada, y el estado (batería/firmware) de
    cada nodo apenas se conecta.
    """

    stage = Signal(str)  # texto de etapa para el banner de estado
    device_status = Signal(object)  # BleDeviceStatus
    measurement_received = Signal(object)  # Measurement
    iteration_completed = Signal(object)  # CalibrationIteration
    finished = Signal(object)  # CalibrationReport
    failed = Signal(str)

    # [Verificado 2026-08-13, hardware real] Por BLE los gaps entre fragmentos
    # llegan a ~590 ms: la capa app debe usar quiet_period_s ~1.5 y un timeout
    # de comando holgado (ver docstring de DwmCliClient).
    #
    # [Bug real, 2026-09-09, hardware real] 10.0s no alcanzaba para INITF
    # específicamente: al arrancar la sesión, el módulo empieza a rankear
    # de inmediato y, durante una ventana de transición, sus notificaciones
    # SESSION_INFO_NTF salen (además de por el canal de streaming dedicado)
    # también por el canal de comandos normal (NUS TX) — el mismo que está
    # entregando el eco multilínea de INITF más su "ok" de cierre. Esa
    # competencia por el mismo canal se confirmó demorando la respuesta
    # completa de INITF hasta ~10.4s en una corrida real (echo+bloque FiRa
    # llegó a los ~8.5s, pero el "ok" final recién a los ~10.4s) — un fallo
    # limpio de "Sin respuesta ... tras 10.0s de espera" pese a que el
    # comando sí se había procesado. Subido con margen real, no arbitrario.
    _QUIET_PERIOD_S = 1.5
    _COMMAND_TIMEOUT_S = 20.0

    def __init__(
        self,
        initiator_info: BleBoardInfo,
        dut_info: BleBoardInfo,
        *,
        real_distance_m: float,
        config: AutocalConfig,
    ) -> None:
        super().__init__()
        self._initiator_info = initiator_info
        self._dut_info = dut_info
        self._real_distance_m = real_distance_m
        self._config = config

    @Slot()
    def run(self) -> None:
        from dwm3001c_cli.transport.ble_link import BleTransport

        initiator_transport: BleTransport | None = None
        dut_transport: BleTransport | None = None
        report: CalibrationReport | None = None
        error: Exception | None = None
        try:
            self.stage.emit(
                f"Conectando a {self._initiator_info.name} "
                f"({self._initiator_info.address}) — rol INITIATOR, referencia…"
            )
            initiator_transport = BleTransport(self._initiator_info.address)
            initiator_transport.open()
            initiator = DwmCliClient(
                initiator_transport,
                quiet_period_s=self._QUIET_PERIOD_S,
                command_timeout_s=self._COMMAND_TIMEOUT_S,
            )
            self.stage.emit(
                f"Conectando a {self._dut_info.name} ({self._dut_info.address}) "
                "— rol RESPONDER, a calibrar…"
            )
            dut_transport = BleTransport(self._dut_info.address)
            dut_transport.open()
            dut = DwmCliClient(
                dut_transport,
                quiet_period_s=self._QUIET_PERIOD_S,
                command_timeout_s=self._COMMAND_TIMEOUT_S,
            )
            # [Mitigación 2026-09-09, hardware real] Las lecturas de batería/firmware
            # se hacen recién acá, con ambas conexiones GATT ya completas y estables,
            # y una después de la otra (nunca en paralelo). Hacerlas apenas se abre
            # cada transporte —como antes— generaba actividad WinRT nativa concurrente
            # en los dos hilos de BleTransport justo durante la ventana de conexión,
            # lo que aumentaba la frecuencia del crash nativo documentado en
            # docs/rama-hardware-ble.md §8 (STATUS_STACK_BUFFER_OVERRUN /
            # 0xC0000409, confirmado con ProcDump contra hardware real). Esto no
            # elimina la causa raíz (bug de threading Qt-STA/bleak-WinRT-MTA), pero
            # reduce la superposición temporal que lo dispara.
            self._emit_device_status("initiator", self._initiator_info, initiator_transport)
            self._emit_device_status("responder", self._dut_info, dut_transport)
            self.stage.emit(f"Calibrando {self._dut_info.name} contra {self._initiator_info.name}…")
            report = autocalibrate(
                dut,
                initiator,
                real_distance_m=self._real_distance_m,
                config=self._config,
                sampler=_ble_sampler,
                on_iteration=self.iteration_completed.emit,
                on_measurement=self.measurement_received.emit,
            )
        except Exception as exc:  # nunca dejar escapar una excepción del worker
            error = exc
        finally:
            for transport in (dut_transport, initiator_transport):
                if transport is None:
                    continue
                try:
                    transport.close()
                except Exception:
                    logger.warning("Error cerrando un transporte BLE al terminar", exc_info=True)
        if error is not None:
            self.failed.emit(str(error))
            return
        assert report is not None
        self.finished.emit(report)

    def _emit_device_status(self, role: str, info: BleBoardInfo, transport: BleTransport) -> None:
        """Lee batería/firmware del puente (best-effort, ver
        ``BleTransport.read_battery_level``/``read_bridge_firmware_version``)
        y emite ``device_status``. Nunca aborta la calibración: si la lectura
        falla del todo, esos métodos ya devuelven ``None`` en vez de lanzar.
        """
        battery_pct = transport.read_battery_level()
        firmware_version = transport.read_bridge_firmware_version()
        self.device_status.emit(
            BleDeviceStatus(
                role=role,
                name=info.name,
                address=info.address,
                battery_pct=battery_pct,
                firmware_version=firmware_version,
            )
        )


class BleMeasureWorker(QObject):
    """Mide distancia entre dos puentes BLE en forma continua, sin calibrar
    (no toca ``ant_delay`` ni escribe nada en NVM), hasta que se pide frenar
    con :meth:`request_stop`.

    Misma base que :class:`BlePairCalibrationWorker` (conexión BLE, arranque
    de sesión FiRa, cierre de transportes siempre) pero sin el bucle de
    ajuste de ``autocalibrate``: arranca ``RESPF``/``INITF`` una sola vez y
    lee mediciones en bloques cortos indefinidamente, para poder reaccionar
    a un pedido de frenado sin esperar un timeout largo.
    """

    stage = Signal(str)
    device_status = Signal(object)  # BleDeviceStatus
    measurement_received = Signal(object)  # Measurement
    finished = Signal(object)  # RangingStats | None (None si no hubo ninguna SUCCESS)
    failed = Signal(str)

    # Ver comentario equivalente en BlePairCalibrationWorker: INITF puede
    # tardar hasta ~10.4s en completar su respuesta (echo + bloque FiRa + ok)
    # por competencia con SESSION_INFO_NTF en el mismo canal de comandos
    # durante la ventana de arranque de la sesión — confirmado con hardware
    # real, 10.0s no alcanzaba.
    _QUIET_PERIOD_S = 1.5
    _COMMAND_TIMEOUT_S = 20.0
    # Bloque corto de lectura (~3 rondas con el BLOCK_MS=200 default de
    # SessionParams): lo bastante chico para notar un request_stop() sin
    # demora perceptible, sin volver el polling tan fino que sature el hilo.
    _POLL_WINDOW_S = 0.6

    def __init__(self, initiator_info: BleBoardInfo, responder_info: BleBoardInfo) -> None:
        super().__init__()
        self._initiator_info = initiator_info
        self._responder_info = responder_info
        # threading.Event, no una señal Qt: mientras run() está bloqueado en
        # su propio bucle, el hilo del worker todavía no llegó a
        # QThread.exec() (ver start_worker), así que una conexión Qt en cola
        # hacia un slot de este worker no se entregaría hasta que run()
        # termine — inútil para frenar el bucle mientras corre. Event.set()
        # sí es seguro para llamar directamente desde el hilo de UI.
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        """Pide terminar la medición en curso (ver comentario de ``_stop_event``)."""
        self._stop_event.set()

    @Slot()
    def run(self) -> None:
        from dwm3001c_cli.transport.ble_link import BleTransport

        initiator_transport: BleTransport | None = None
        responder_transport: BleTransport | None = None
        initiator: DwmCliClient | None = None
        responder: DwmCliClient | None = None
        error: Exception | None = None
        successes: list[int] = []
        received = 0
        try:
            self.stage.emit(
                f"Conectando a {self._initiator_info.name} "
                f"({self._initiator_info.address}) — rol INITIATOR…"
            )
            initiator_transport = BleTransport(self._initiator_info.address)
            initiator_transport.open()
            initiator = DwmCliClient(
                initiator_transport,
                quiet_period_s=self._QUIET_PERIOD_S,
                command_timeout_s=self._COMMAND_TIMEOUT_S,
            )
            self.stage.emit(
                f"Conectando a {self._responder_info.name} "
                f"({self._responder_info.address}) — rol RESPONDER…"
            )
            responder_transport = BleTransport(self._responder_info.address)
            responder_transport.open()
            responder = DwmCliClient(
                responder_transport,
                quiet_period_s=self._QUIET_PERIOD_S,
                command_timeout_s=self._COMMAND_TIMEOUT_S,
            )
            # [Mitigación 2026-09-09] Ver comentario equivalente en
            # BlePairCalibrationWorker.run(): las dos lecturas de
            # batería/firmware van juntas, recién con ambas conexiones
            # BLE estables, para no aumentar la frecuencia del crash
            # nativo documentado en docs/rama-hardware-ble.md §8.
            self._emit_device_status("initiator", self._initiator_info, initiator_transport)
            self._emit_device_status("responder", self._responder_info, responder_transport)

            self.stage.emit('Midiendo… ("Frenar medición" para terminar)')
            params = SessionParams()
            responder.ensure_mode_none()
            initiator.ensure_mode_none()
            responder.start_respf(**params.responder_kwargs())
            initiator.start_initf(**params.initiator_kwargs())

            while not self._stop_event.is_set():
                for measurement in initiator.read_notifications(
                    duration_s=self._POLL_WINDOW_S, max_count=1
                ):
                    received += 1
                    self.measurement_received.emit(measurement)
                    if measurement.status == "SUCCESS" and measurement.distance_cm is not None:
                        successes.append(measurement.distance_cm)
        except Exception as exc:  # nunca dejar escapar una excepción del worker
            error = exc
        finally:
            if initiator is not None and responder is not None:
                cleanup_both(initiator, responder)
            for transport in (responder_transport, initiator_transport):
                if transport is None:
                    continue
                try:
                    transport.close()
                except Exception:
                    logger.warning("Error cerrando un transporte BLE al terminar", exc_info=True)
        if error is not None:
            self.failed.emit(str(error))
            return
        stats: RangingStats | None = None
        if successes:
            stats = RangingStats(
                n_requested=received,
                n_received=received,
                n_success=len(successes),
                mean_cm=statistics.fmean(successes),
                std_cm=statistics.pstdev(successes),
                min_cm=min(successes),
                max_cm=max(successes),
            )
        self.finished.emit(stats)

    def _emit_device_status(self, role: str, info: BleBoardInfo, transport: BleTransport) -> None:
        """Ver ``BlePairCalibrationWorker._emit_device_status`` (misma lógica,
        duplicada para no acoplar los dos workers entre sí)."""
        battery_pct = transport.read_battery_level()
        firmware_version = transport.read_bridge_firmware_version()
        self.device_status.emit(
            BleDeviceStatus(
                role=role,
                name=info.name,
                address=info.address,
                battery_pct=battery_pct,
                firmware_version=firmware_version,
            )
        )


class CalibrationWorker(QObject):
    """Corre ``autocalibrate`` con progreso en vivo por iteración."""

    iteration_completed = Signal(object)  # CalibrationIteration
    finished = Signal(object)  # CalibrationReport
    failed = Signal(str)

    def __init__(
        self,
        device: DwmCliClient,
        reference: DwmCliClient,
        *,
        real_distance_m: float,
        config: AutocalConfig,
    ) -> None:
        super().__init__()
        self._device = device
        self._reference = reference
        self._real_distance_m = real_distance_m
        self._config = config

    @Slot()
    def run(self) -> None:
        try:
            report: CalibrationReport = autocalibrate(
                self._device,
                self._reference,
                real_distance_m=self._real_distance_m,
                config=self._config,
                on_iteration=self.iteration_completed.emit,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(report)


class _RunnableWorker(Protocol):
    """Estructura mínima que necesita ``start_worker``: un ``QObject`` con un
    método ``run()`` decorado ``@Slot()`` (todos los workers de este módulo).
    """

    def run(self) -> None: ...
    def moveToThread(self, thread: QThread, /) -> bool: ...


def start_worker(worker: _RunnableWorker) -> QThread:
    """Corre ``worker.run()`` en un ``QThread`` dedicado.

    Devuelve el ``QThread`` sin iniciar todavía, para que el caller pueda
    conectar señales adicionales del worker antes de llamar a ``.start()``.
    El caller es responsable de mantener referencias vivas a ``worker`` y al
    thread devuelto (p. ej. como atributos de instancia) hasta que terminen —
    si Python los recolecta antes, Qt puede crashear.
    """
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    thread.finished.connect(thread.deleteLater)
    return thread


__all__ = [
    "BleDeviceStatus",
    "BleMeasureWorker",
    "BlePairCalibrationWorker",
    "BleScanWorker",
    "BoardPort",
    "CalibrationIteration",
    "CalibrationWorker",
    "CallableWorker",
    "ConnectWorker",
    "ScanWorker",
    "TerminalWorker",
    "ValidationWorker",
    "format_ble_device_status",
    "start_worker",
]
