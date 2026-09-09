"""Workers ``QObject`` para mover E/S bloqueante (serie o BLE) fuera del hilo
de UI (plan F9): se mueven a un ``QThread`` con ``moveToThread`` (no herencia
de ``QThread``, no ``QThreadPool``), porque necesitan vida larga con señales
de progreso continuas. Ninguno importa Qt widgets — solo ``QtCore``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from dwm3001c_cli.calibration.autocal import (
    AutocalConfig,
    CalibrationIteration,
    CalibrationReport,
    autocalibrate,
)
from dwm3001c_cli.calibration.sampler import SessionParams, collect_samples
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


class BlePairCalibrationWorker(QObject):
    """Calibración con **ambas placas por Bluetooth** (puentes nRF52840).

    Abre los dos ``BleTransport``, corre ``autocalibrate`` con
    :func:`_ble_sampler` y cierra ambos transportes siempre, incluso ante
    error. Emite por señal cada medición recibida (para mostrar la distancia
    en vivo) y cada iteración completada.
    """

    stage = Signal(str)  # texto de etapa para el banner de estado
    measurement_received = Signal(object)  # Measurement
    iteration_completed = Signal(object)  # CalibrationIteration
    finished = Signal(object)  # CalibrationReport
    failed = Signal(str)

    # [Verificado 2026-08-13, hardware real] Por BLE los gaps entre fragmentos
    # llegan a ~590 ms: la capa app debe usar quiet_period_s ~1.5 y un timeout
    # de comando holgado (ver docstring de DwmCliClient).
    _QUIET_PERIOD_S = 1.5
    _COMMAND_TIMEOUT_S = 10.0

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
    "BlePairCalibrationWorker",
    "BleScanWorker",
    "BoardPort",
    "CalibrationIteration",
    "CalibrationWorker",
    "ConnectWorker",
    "ScanWorker",
    "TerminalWorker",
    "ValidationWorker",
    "start_worker",
]
