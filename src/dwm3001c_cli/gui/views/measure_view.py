"""Vista de medición continua con **ambas placas por Bluetooth** (pestaña "Medir").

A diferencia de :class:`~dwm3001c_cli.gui.views.ble_calibration_view.BleCalibrationView`,
esta vista no calibra nada (no toca ``ant_delay`` ni escribe en NVM): arranca
una sesión TWR entre los dos nodos elegidos y muestra la distancia medida en
vivo (con su desviación) hasta que se pide frenar. Mismo patrón de escaneo +
filtro + selección de dos nodos, y misma información de batería/firmware del
puente al conectar (ver ``BleDeviceStatus``).
"""

from __future__ import annotations

import statistics
from collections import deque
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dwm3001c_cli.core.models import Measurement, RangingStats
from dwm3001c_cli.gui.workers import (
    BleDeviceStatus,
    BleMeasureWorker,
    BleScanWorker,
    format_ble_device_status,
    start_worker,
)

if TYPE_CHECKING:
    from dwm3001c_cli.transport.ble_discovery import BleBoardInfo

# Mismos colores que BleCalibrationView, para que el banner de estado se vea
# igual en toda la app.
_STYLE_WORKING = "background-color: #fff3cd; color: #664d03; padding: 6px; border-radius: 4px;"
_STYLE_OK = "background-color: #d1e7dd; color: #0f5132; padding: 6px; border-radius: 4px;"
_STYLE_ERROR = "background-color: #f8d7da; color: #842029; padding: 6px; border-radius: 4px;"
_STYLE_IDLE = ""

_STYLE_PRIMARY_BUTTON = """
    QPushButton {
        background-color: #0d6efd;
        color: white;
        font-weight: bold;
        padding: 8px 20px;
        border-radius: 4px;
        border: none;
    }
    QPushButton:hover { background-color: #0b5ed7; }
    QPushButton:pressed { background-color: #0a58ca; }
    QPushButton:disabled { background-color: #a9c6fb; color: #eef4ff; }
"""
_STYLE_STOP_BUTTON = """
    QPushButton {
        background-color: #dc3545;
        color: white;
        font-weight: bold;
        padding: 8px 20px;
        border-radius: 4px;
        border: none;
    }
    QPushButton:hover { background-color: #bb2d3b; }
    QPushButton:pressed { background-color: #b02a37; }
    QPushButton:disabled { background-color: #efb3b8; color: #fdf0f1; }
"""

_N_LIVE_WINDOW = 20  # ventana de media/desvío móvil de la distancia en vivo
_NO_DEVICE_STATUS = "—"


class MeasureView(QWidget):
    """Mide distancia entre dos nodos BLE en vivo, sin calibrar."""

    def __init__(self) -> None:
        super().__init__()
        self._devices: list[BleBoardInfo] = []
        self._thread: QThread | None = None
        self._worker: BleMeasureWorker | None = None
        self._scan_worker: BleScanWorker | None = None
        self._recent_cm: deque[int] = deque(maxlen=_N_LIVE_WINDOW)
        # Flag plano (no ``_thread.isRunning()``: el objeto C++ del QThread se
        # destruye vía deleteLater al terminar y consultarlo crashea).
        self._running = False

        layout = QVBoxLayout(self)

        # --- Fila de escaneo + filtro -------------------------------------
        scan_row = QHBoxLayout()
        self._scan_btn = QPushButton("Escanear BLE")
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        scan_row.addWidget(self._scan_btn)
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filtrar dispositivos por nombre o dirección…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._populate_device_views)
        scan_row.addWidget(self._filter_edit, 1)
        layout.addLayout(scan_row)

        # --- Lista de dispositivos encontrados -----------------------------
        self._device_list = QListWidget()
        self._device_list.setToolTip(
            "Dispositivos Bluetooth encontrados (el filtro de arriba los acota)."
        )
        layout.addWidget(self._device_list, 1)

        # --- Selección de los dos nodos ------------------------------------
        form = QFormLayout()
        self._initiator_combo = QComboBox()
        self._initiator_combo.setToolTip("Rol INITIATOR de la sesión TWR.")
        self._initiator_combo.currentIndexChanged.connect(self._update_selection_state)
        form.addRow("Nodo INITIATOR:", self._initiator_combo)
        self._initiator_device_label = QLabel(_NO_DEVICE_STATUS)
        self._initiator_device_label.setToolTip(
            "Batería y versión de firmware del puente — disponibles recién al conectar."
        )
        form.addRow("Estado del nodo:", self._initiator_device_label)

        self._responder_combo = QComboBox()
        self._responder_combo.setToolTip("Rol RESPONDER de la sesión TWR.")
        self._responder_combo.currentIndexChanged.connect(self._update_selection_state)
        form.addRow("Nodo RESPONDER:", self._responder_combo)
        self._responder_device_label = QLabel(_NO_DEVICE_STATUS)
        self._responder_device_label.setToolTip(
            "Batería y versión de firmware del puente — disponibles recién al conectar."
        )
        form.addRow("Estado del nodo:", self._responder_device_label)

        self._selection_label = QLabel("Seleccione los dos nodos a medir.")
        self._selection_label.setStyleSheet("font-weight: bold;")
        form.addRow("", self._selection_label)
        layout.addLayout(form)

        # --- Arranque/frenado + banner de estado ----------------------------
        run_row = QHBoxLayout()
        self._start_btn = QPushButton("Iniciar medición")
        self._start_btn.setStyleSheet(_STYLE_PRIMARY_BUTTON)
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._start_btn.setEnabled(False)
        run_row.addWidget(self._start_btn)
        self._stop_btn = QPushButton("Frenar medición")
        self._stop_btn.setStyleSheet(_STYLE_STOP_BUTTON)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        self._stop_btn.setEnabled(False)
        run_row.addWidget(self._stop_btn)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminado: visible solo en proceso
        self._progress.hide()
        run_row.addWidget(self._progress, 1)
        layout.addLayout(run_row)

        self._status_label = QLabel(
            "Escanee los dispositivos BLE y seleccione los dos nodos para empezar."
        )
        self._status_label.setWordWrap(True)
        self._set_status("idle", self._status_label.text())
        layout.addWidget(self._status_label)

        # --- Distancia medida en vivo --------------------------------------
        self._live_label = QLabel("Distancia: —")
        self._live_label.setStyleSheet("font-size: 13pt; font-weight: bold;")
        layout.addWidget(self._live_label)

        # --- Log ------------------------------------------------------------
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(1000)
        layout.addWidget(self._log, 1)

    # ------------------------------------------------------------------ escaneo

    def _on_scan_clicked(self) -> None:
        self._scan_btn.setEnabled(False)
        self._set_status("working", "Escaneando dispositivos BLE…")
        self._scan_worker = BleScanWorker()
        thread = start_worker(self._scan_worker)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.failed.connect(self._on_scan_failed)
        self._scan_worker.finished.connect(thread.quit)
        self._scan_worker.failed.connect(thread.quit)
        self._thread = thread
        thread.start()

    def _on_scan_finished(self, devices: list[BleBoardInfo]) -> None:
        self._devices = devices
        self._scan_btn.setEnabled(True)
        self._populate_device_views()
        if len(devices) < 2:
            self._set_status(
                "error",
                f"Se encontraron {len(devices)} dispositivo(s) BLE. Se necesitan 2: "
                "verifique que ambos puentes nRF52840 estén encendidos.",
            )
            return
        self._initiator_combo.setCurrentIndex(0)
        self._responder_combo.setCurrentIndex(1)
        self._set_status(
            "idle",
            f"Encontrados {len(devices)} dispositivos BLE. Seleccione los dos nodos.",
        )

    def _on_scan_failed(self, message: str) -> None:
        self._scan_btn.setEnabled(True)
        self._set_status("error", f"Error escaneando BLE: {message}")

    # ------------------------------------------------------------ selección

    def _populate_device_views(self) -> None:
        needle = self._filter_edit.text().strip().lower()
        visible = [
            device
            for device in self._devices
            if not needle or needle in device.name.lower() or needle in device.address.lower()
        ]
        self._device_list.clear()
        for device in visible:
            rssi = f", {device.rssi} dBm" if device.rssi is not None else ""
            self._device_list.addItem(f"{device.name} — {device.address}{rssi}")

        previous_initiator = self._current_address(self._initiator_combo)
        previous_responder = self._current_address(self._responder_combo)
        for combo in (self._initiator_combo, self._responder_combo):
            combo.blockSignals(True)
            combo.clear()
            for device in visible:
                combo.addItem(f"{device.name} ({device.address})", device.address)
            combo.blockSignals(False)
        for combo, previous in (
            (self._initiator_combo, previous_initiator),
            (self._responder_combo, previous_responder),
        ):
            index = combo.findData(previous)
            if index >= 0:
                combo.setCurrentIndex(index)
        self._update_selection_state()

    @staticmethod
    def _current_address(combo: QComboBox) -> str | None:
        return combo.currentData() if combo.count() else None

    def _device_by_address(self, address: str | None) -> BleBoardInfo | None:
        if address is None:
            return None
        return next((d for d in self._devices if d.address == address), None)

    def _update_selection_state(self) -> None:
        initiator = self._device_by_address(self._current_address(self._initiator_combo))
        responder = self._device_by_address(self._current_address(self._responder_combo))
        distinct = (
            initiator is not None
            and responder is not None
            and initiator.address != responder.address
        )
        self._start_btn.setEnabled(distinct and not self.is_running)
        if initiator is not None and responder is not None and not distinct:
            self._selection_label.setText("Los dos nodos deben ser dispositivos distintos.")
        elif initiator is not None and responder is not None:
            self._selection_label.setText(
                f"INITIATOR: {initiator.name} ({initiator.address})   ·   "
                f"RESPONDER: {responder.name} ({responder.address})"
            )

    # ------------------------------------------------------------- ejecución

    def _on_start_clicked(self) -> None:
        initiator = self._device_by_address(self._current_address(self._initiator_combo))
        responder = self._device_by_address(self._current_address(self._responder_combo))
        if initiator is None or responder is None or initiator.address == responder.address:
            return

        self._recent_cm.clear()
        self._live_label.setText("Distancia: —")
        self._log.clear()
        self._initiator_device_label.setText(_NO_DEVICE_STATUS)
        self._responder_device_label.setText(_NO_DEVICE_STATUS)
        self._running = True
        self._set_inputs_enabled(False)
        self._progress.show()
        self._set_status("working", "Conectando por BLE…")

        worker = BleMeasureWorker(initiator, responder)
        thread = start_worker(worker)
        worker.stage.connect(lambda text: self._set_status("working", text))
        worker.device_status.connect(self._on_device_status)
        worker.measurement_received.connect(self._on_measurement)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_stop_clicked(self) -> None:
        if self._worker is not None:
            self._set_status("working", "Frenando medición…")
            self._stop_btn.setEnabled(False)
            self._worker.request_stop()

    def _on_device_status(self, status: BleDeviceStatus) -> None:
        label = (
            self._initiator_device_label
            if status.role == "initiator"
            else self._responder_device_label
        )
        label.setText(format_ble_device_status(status.battery_pct, status.firmware_version))

    def _on_measurement(self, measurement: Measurement) -> None:
        if measurement.status == "SUCCESS" and measurement.distance_cm is not None:
            self._recent_cm.append(measurement.distance_cm)
            mean = statistics.fmean(self._recent_cm)
            std = statistics.pstdev(self._recent_cm) if len(self._recent_cm) > 1 else 0.0
            self._live_label.setText(
                f"Distancia: {measurement.distance_cm} cm   ·   "
                f"media (últimas {len(self._recent_cm)}): {mean:.1f} cm   ·   "
                f"desvío: {std:.1f} cm"
            )
        else:
            self._live_label.setText(f"Distancia: sin éxito en esta ronda ({measurement.status})")

    def _on_finished(self, stats: RangingStats | None) -> None:
        self._running = False
        self._progress.hide()
        self._set_inputs_enabled(True)
        if stats is None:
            self._set_status("idle", "Medición terminada sin datos SUCCESS.")
            self._log.appendPlainText("Medición terminada: sin mediciones SUCCESS.")
            return
        self._set_status(
            "ok",
            f"Medición terminada: {stats.n_success} muestras SUCCESS de {stats.n_received} "
            f"recibidas, media {stats.mean_cm:.1f} cm, desvío {stats.std_cm:.1f} cm.",
        )
        self._log.appendPlainText(
            f"Resumen: n={stats.n_success}/{stats.n_received}  "
            f"media={stats.mean_cm:.1f} cm  desvío={stats.std_cm:.1f} cm  "
            f"min={stats.min_cm} cm  max={stats.max_cm} cm"
        )

    def _on_failed(self, message: str) -> None:
        self._running = False
        self._progress.hide()
        self._set_inputs_enabled(True)
        self._set_status("error", f"✖ Error: {message}")
        self._log.appendPlainText(f"[error] {message}")

    # -------------------------------------------------------------- utilidades

    @property
    def is_running(self) -> bool:
        """``True`` mientras hay una medición (o escaneo) en curso."""
        return self._running

    def _set_inputs_enabled(self, enabled: bool) -> None:
        self._scan_btn.setEnabled(enabled)
        self._filter_edit.setEnabled(enabled)
        self._initiator_combo.setEnabled(enabled)
        self._responder_combo.setEnabled(enabled)
        self._stop_btn.setEnabled(not enabled)
        if enabled:
            self._update_selection_state()

    def _set_status(self, state: str, text: str) -> None:
        styles = {
            "idle": _STYLE_IDLE,
            "working": _STYLE_WORKING,
            "ok": _STYLE_OK,
            "error": _STYLE_ERROR,
        }
        self._status_label.setText(text)
        self._status_label.setStyleSheet(styles[state])
