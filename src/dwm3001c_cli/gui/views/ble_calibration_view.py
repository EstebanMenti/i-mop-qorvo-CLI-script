"""Vista de calibración con **ambas placas por Bluetooth** (puentes nRF52840).

Flujo (pestaña "Calibración BLE" de ``dwm-gui``):

1. "Escanear BLE" lista **todos** los dispositivos Bluetooth al alcance.
2. El campo de texto filtra la lista por substring (nombre o dirección).
3. Se seleccionan dos dispositivos en los combos: INITIATOR (referencia, no se
   toca) y RESPONDER (**el que se calibra** — siempre queda a la vista cuál es).
4. Se ingresa la distancia real entre nodos y se arranca.
5. Mientras corre, un banner de estado con color muestra la etapa (conectando →
   calibrando → guardando → terminada) y la **distancia medida en vivo**.

A diferencia de :class:`~dwm3001c_cli.gui.views.calibration_view.CalibrationView`,
esta vista es autónoma: no recibe clientes conectados en la pestaña "Conexión",
sino que abre y cierra sus propios transportes BLE dentro del worker (que los
cierra siempre, incluso ante error).
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dwm3001c_cli.calibration.autocal import (
    AutocalConfig,
    CalibrationIteration,
    CalibrationReport,
)
from dwm3001c_cli.core.models import Measurement
from dwm3001c_cli.gui.workers import BlePairCalibrationWorker, BleScanWorker, start_worker

if TYPE_CHECKING:
    from dwm3001c_cli.transport.ble_discovery import BleBoardInfo

# Estados del banner de estado: claro de un vistazo en qué etapa está la
# calibración (en proceso = ámbar; terminada bien = verde; error = rojo).
_STYLE_WORKING = "background-color: #fff3cd; color: #664d03; padding: 6px; border-radius: 4px;"
_STYLE_OK = "background-color: #d1e7dd; color: #0f5132; padding: 6px; border-radius: 4px;"
_STYLE_ERROR = "background-color: #f8d7da; color: #842029; padding: 6px; border-radius: 4px;"
_STYLE_IDLE = ""

_N_LIVE_WINDOW = 20  # ventana de media móvil de la distancia en vivo


class BleCalibrationView(QWidget):
    """Calibración de antenna delay con INITIATOR y RESPONDER ambos por BLE."""

    def __init__(self) -> None:
        super().__init__()
        self._devices: list[BleBoardInfo] = []
        self._thread: QThread | None = None
        self._worker: BlePairCalibrationWorker | None = None
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
        self._initiator_combo.setToolTip("Referencia: corre INITF y NO se modifica.")
        self._initiator_combo.currentIndexChanged.connect(self._update_selection_state)
        form.addRow("Nodo INITIATOR (referencia):", self._initiator_combo)

        self._dut_combo = QComboBox()
        self._dut_combo.setToolTip("RESPONDER: es la placa que SE CALIBRA (se modifica).")
        self._dut_combo.currentIndexChanged.connect(self._update_selection_state)
        form.addRow("Nodo RESPONDER (a calibrar):", self._dut_combo)

        self._dut_label = QLabel("Seleccione los dos nodos a calibrar.")
        self._dut_label.setStyleSheet("font-weight: bold;")
        form.addRow("", self._dut_label)

        self._distance_spin = QDoubleSpinBox()
        self._distance_spin.setRange(0.5, 100.0)
        self._distance_spin.setSuffix(" m")
        self._distance_spin.setValue(2.0)
        form.addRow("Distancia real entre nodos:", self._distance_spin)

        self._samples_spin = QSpinBox()
        self._samples_spin.setRange(10, 1000)
        self._samples_spin.setValue(100)
        form.addRow("Muestras por medición:", self._samples_spin)

        self._tolerance_spin = QDoubleSpinBox()
        self._tolerance_spin.setRange(0.1, 50.0)
        self._tolerance_spin.setValue(2.0)
        self._tolerance_spin.setSuffix(" cm")
        form.addRow("Tolerancia:", self._tolerance_spin)

        self._max_iterations_spin = QSpinBox()
        self._max_iterations_spin.setRange(1, 30)
        self._max_iterations_spin.setValue(6)
        form.addRow("Iteraciones máximas:", self._max_iterations_spin)

        self._save_check = QCheckBox("Guardar en NVM al converger (SAVE)")
        self._save_check.setChecked(True)
        form.addRow("", self._save_check)
        layout.addLayout(form)

        # --- Arranque + banner de estado -----------------------------------
        run_row = QHBoxLayout()
        self._run_btn = QPushButton("Iniciar calibración BLE")
        self._run_btn.clicked.connect(self._on_run_clicked)
        self._run_btn.setEnabled(False)
        run_row.addWidget(self._run_btn)
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
        self._live_label = QLabel("Distancia medida: —")
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
        self._running = True
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
        self._running = False
        self._populate_device_views()
        if len(devices) < 2:
            self._set_status(
                "error",
                f"Se encontraron {len(devices)} dispositivo(s) BLE. Se necesitan 2: "
                "verifique que ambos puentes nRF52840 estén encendidos.",
            )
            return
        # Preselección: primero INITIATOR, segundo RESPONDER (el usuario puede
        # cambiar ambos; lo único forzado es que sean distintos).
        self._initiator_combo.setCurrentIndex(0)
        self._dut_combo.setCurrentIndex(1)
        self._set_status(
            "idle",
            f"Encontrados {len(devices)} dispositivos BLE. Seleccione los dos nodos "
            "y la distancia real.",
        )

    def _on_scan_failed(self, message: str) -> None:
        self._scan_btn.setEnabled(True)
        self._running = False
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

        # Combos: repoblar conservando la selección previa por dirección.
        previous_initiator = self._current_address(self._initiator_combo)
        previous_dut = self._current_address(self._dut_combo)
        for combo in (self._initiator_combo, self._dut_combo):
            combo.blockSignals(True)
            combo.clear()
            for device in visible:
                combo.addItem(f"{device.name} ({device.address})", device.address)
            combo.blockSignals(False)
        for combo, previous in (
            (self._initiator_combo, previous_initiator),
            (self._dut_combo, previous_dut),
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
        dut = self._device_by_address(self._current_address(self._dut_combo))
        distinct = initiator is not None and dut is not None and initiator.address != dut.address
        self._run_btn.setEnabled(distinct and not self.is_running)
        if initiator is not None and dut is not None:
            if distinct:
                self._dut_label.setText(
                    f"SE CALIBRA (RESPONDER): {dut.name} ({dut.address}) — "
                    f"referencia INITIATOR: {initiator.name} ({initiator.address})"
                )
            else:
                self._dut_label.setText("Los dos nodos deben ser dispositivos distintos.")
                self._run_btn.setEnabled(False)
        elif dut is not None:
            self._dut_label.setText(f"SE CALIBRA (RESPONDER): {dut.name} ({dut.address})")

    # ------------------------------------------------------------- ejecución

    def _on_run_clicked(self) -> None:
        initiator = self._device_by_address(self._current_address(self._initiator_combo))
        dut = self._device_by_address(self._current_address(self._dut_combo))
        if initiator is None or dut is None or initiator.address == dut.address:
            return
        config = AutocalConfig(
            n_samples=self._samples_spin.value(),
            tolerance_cm=self._tolerance_spin.value(),
            max_iterations=self._max_iterations_spin.value(),
            do_save=self._save_check.isChecked(),
        )
        confirmed = QMessageBox.question(
            self,
            "Confirmar calibración",
            f"Se va a calibrar {config.key} en {dut.name} ({dut.address}), contra "
            f"{initiator.name} ({initiator.address}) a "
            f"{self._distance_spin.value():.2f} m. ¿Continuar?",
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            self._set_status("idle", "Calibración cancelada.")
            return

        self._recent_cm.clear()
        self._live_label.setText("Distancia medida: —")
        self._log.clear()
        self._running = True
        self._set_inputs_enabled(False)
        self._progress.show()
        self._set_status("working", "Conectando por BLE…")

        worker = BlePairCalibrationWorker(
            initiator,
            dut,
            real_distance_m=self._distance_spin.value(),
            config=config,
        )
        thread = start_worker(worker)
        worker.stage.connect(lambda text: self._set_status("working", text))
        worker.measurement_received.connect(self._on_measurement)
        worker.iteration_completed.connect(self._on_iteration)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_measurement(self, measurement: Measurement) -> None:
        if measurement.status == "SUCCESS" and measurement.distance_cm is not None:
            self._recent_cm.append(measurement.distance_cm)
            mean = sum(self._recent_cm) / len(self._recent_cm)
            self._live_label.setText(
                f"Distancia medida: {measurement.distance_cm} cm   ·   "
                f"media (últimas {len(self._recent_cm)}): {mean:.1f} cm"
            )
        else:
            self._live_label.setText(
                f"Distancia medida: sin éxito en esta ronda ({measurement.status})"
            )

    def _on_iteration(self, iteration: CalibrationIteration) -> None:
        correction = (
            f", corrección {iteration.correction_units:+d}"
            if iteration.correction_units is not None
            else ""
        )
        self._log.appendPlainText(
            f"Iteración {iteration.index}: delay={iteration.delay}  "
            f"media={iteration.mean_cm:.1f} cm  desvío={iteration.std_cm:.1f} cm  "
            f"error={iteration.error_cm:+.1f} cm{correction}"
        )
        self._set_status(
            "working",
            f"Calibrando… iteración {iteration.index + 1} completada "
            f"(media {iteration.mean_cm:.1f} cm, error {iteration.error_cm:+.1f} cm).",
        )

    def _on_finished(self, report: CalibrationReport) -> None:
        self._running = False
        self._progress.hide()
        self._set_inputs_enabled(True)
        saved = "guardado en NVM (SAVE)" if report.saved else "SIN guardar"
        self._set_status(
            "ok",
            f"✔ Calibración terminada: {report.key} {report.initial_delay} → "
            f"{report.final_delay} en {report.device_port} ({saved}). "
            "Las placas quedaron en modo NONE.",
        )
        self._log.appendPlainText(
            f"Convergió: delay {report.initial_delay} -> {report.final_delay} ({saved})"
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
        """``True`` mientras hay una calibración (o escaneo) en curso."""
        return self._running

    def _set_inputs_enabled(self, enabled: bool) -> None:
        self._scan_btn.setEnabled(enabled)
        self._filter_edit.setEnabled(enabled)
        self._initiator_combo.setEnabled(enabled)
        self._dut_combo.setEnabled(enabled)
        self._distance_spin.setEnabled(enabled)
        self._samples_spin.setEnabled(enabled)
        self._tolerance_spin.setEnabled(enabled)
        self._max_iterations_spin.setEnabled(enabled)
        self._save_check.setEnabled(enabled)
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
