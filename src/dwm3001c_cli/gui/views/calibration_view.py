"""Vista de calibración: corre ``autocalibrate`` con gráfico de convergencia en vivo."""

from __future__ import annotations

from pyqtgraph import InfiniteLine, PlotWidget, mkPen
from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dwm3001c_cli.calibration.autocal import AutocalConfig, CalibrationIteration, CalibrationReport
from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.core.errors import Dwm3001cError
from dwm3001c_cli.gui.workers import CalibrationWorker, start_worker


class CalibrationView(QWidget):
    """Análogo a ``dwm calibrate``: RESPONDER es la placa a calibrar (RESPF),
    INITIATOR es la referencia (INITF), igual que en la CLI.
    """

    def __init__(self) -> None:
        super().__init__()
        self._initiator_client: DwmCliClient | None = None
        self._responder_client: DwmCliClient | None = None
        self._thread: QThread | None = None
        self._worker: CalibrationWorker | None = None
        self._iteration_x: list[int] = []
        self._iteration_y: list[float] = []

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._distance_spin = QDoubleSpinBox()
        self._distance_spin.setRange(0.1, 100.0)
        self._distance_spin.setSuffix(" m")
        self._distance_spin.setValue(2.0)
        form.addRow("Distancia real:", self._distance_spin)

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

        self._channel_spin = QSpinBox()
        self._channel_spin.setRange(5, 9)
        self._channel_spin.setValue(9)
        form.addRow("Canal FiRa:", self._channel_spin)

        self._save_check = QCheckBox("Guardar en NVM al converger (SAVE)")
        self._save_check.setChecked(True)
        form.addRow("", self._save_check)

        layout.addLayout(form)

        top_row = QHBoxLayout()
        self._run_btn = QPushButton("Calibrar")
        self._run_btn.clicked.connect(self._on_run_clicked)
        self._run_btn.setEnabled(False)
        self._status_label = QLabel("Conectá INITIATOR y RESPONDER para empezar.")
        top_row.addWidget(self._run_btn)
        top_row.addWidget(self._status_label, 1)
        layout.addLayout(top_row)

        self._plot = PlotWidget()
        self._plot.setLabel("left", "Distancia medida [cm]")
        self._plot.setLabel("bottom", "Iteración")
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._measured_curve = self._plot.plot([], [], pen=None, symbol="o", symbolBrush="b")
        self._target_line = InfiniteLine(angle=0, pen=mkPen("g", style=Qt.PenStyle.DashLine))
        self._plot.addItem(self._target_line)
        layout.addWidget(self._plot, 1)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(500)
        layout.addWidget(self._log)

    def set_initiator(self, client: DwmCliClient) -> None:
        self._initiator_client = client
        self._update_status()

    def set_responder(self, client: DwmCliClient) -> None:
        self._responder_client = client
        self._update_status()

    def _update_status(self) -> None:
        initiator, responder = self._initiator_client, self._responder_client
        ready = initiator is not None and responder is not None
        self._run_btn.setEnabled(ready)
        if initiator is not None and responder is not None:
            self._status_label.setText(
                f"Listo: referencia {initiator.name} · a calibrar {responder.name}"
            )

    def _on_run_clicked(self) -> None:
        if self._initiator_client is None or self._responder_client is None:
            return
        config = AutocalConfig(
            n_samples=self._samples_spin.value(),
            tolerance_cm=self._tolerance_spin.value(),
            max_iterations=self._max_iterations_spin.value(),
            channel=self._channel_spin.value(),
            do_save=self._save_check.isChecked(),
        )
        try:
            current = self._responder_client.calkey_read(config.key)
        except Dwm3001cError as exc:
            self._status_label.setText(f"Error leyendo {config.key}: {exc}")
            return

        confirmed = QMessageBox.question(
            self,
            "Confirmar calibración",
            f"Se va a calibrar {config.key} en {self._responder_client.name} "
            f"(valor actual: {current.value}). ¿Continuar?",
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            self._status_label.setText("Calibración cancelada.")
            return

        self._iteration_x = []
        self._iteration_y = []
        self._measured_curve.setData([], [])
        self._target_line.setValue(self._distance_spin.value() * 100.0)
        self._log.clear()
        self._run_btn.setEnabled(False)
        self._status_label.setText("Calibrando...")

        worker = CalibrationWorker(
            self._responder_client,
            self._initiator_client,
            real_distance_m=self._distance_spin.value(),
            config=config,
        )
        thread = start_worker(worker)
        worker.iteration_completed.connect(self._on_iteration)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_iteration(self, iteration: CalibrationIteration) -> None:
        self._iteration_x.append(iteration.index)
        self._iteration_y.append(iteration.mean_cm)
        self._measured_curve.setData(self._iteration_x, self._iteration_y)
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

    def _on_finished(self, report: CalibrationReport) -> None:
        self._run_btn.setEnabled(True)
        self._status_label.setText(
            f"Convergió: {report.key} {report.initial_delay} -> {report.final_delay} "
            f"({'guardado en NVM' if report.saved else 'sin guardar'})"
        )

    def _on_failed(self, message: str) -> None:
        self._run_btn.setEnabled(True)
        self._status_label.setText(f"Error: {message}")
        self._log.appendPlainText(f"[error] {message}")
