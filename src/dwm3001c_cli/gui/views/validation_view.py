"""Vista de validación: corre la suite de comandos y muestra progreso en vivo."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTableView, QVBoxLayout, QWidget

from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.core.errors import Dwm3001cError
from dwm3001c_cli.core.models import DeviceInfo, ValidationResult
from dwm3001c_cli.gui.models import ValidationResultsModel
from dwm3001c_cli.gui.workers import ValidationWorker, start_worker
from dwm3001c_cli.validation.report import summarize, write_reports


class ValidationView(QWidget):
    """Análogo a ``dwm validate``: INITIATOR como placa principal, RESPONDER
    (USB o BLE) como segunda placa opcional, habilita el check C4 (TWR real).
    """

    def __init__(self) -> None:
        super().__init__()
        self._initiator_client: DwmCliClient | None = None
        self._responder_client: DwmCliClient | None = None
        self._thread: QThread | None = None
        self._worker: ValidationWorker | None = None

        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        self._run_btn = QPushButton("Ejecutar validación")
        self._run_btn.clicked.connect(self._on_run_clicked)
        self._run_btn.setEnabled(False)
        self._status_label = QLabel("Conectá al menos la placa INITIATOR para empezar.")
        top_row.addWidget(self._run_btn)
        top_row.addWidget(self._status_label, 1)
        layout.addLayout(top_row)

        self._model = ValidationResultsModel()
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table, 1)

        self._report_label = QLabel("")
        self._report_label.setWordWrap(True)
        layout.addWidget(self._report_label)

    def set_initiator(self, client: DwmCliClient) -> None:
        self._initiator_client = client
        self._update_status()

    def set_responder(self, client: DwmCliClient) -> None:
        self._responder_client = client
        self._update_status()

    def _update_status(self) -> None:
        ready = self._initiator_client is not None
        self._run_btn.setEnabled(ready)
        if not ready:
            return
        second = (
            " + RESPONDER (habilita el check C4, sesión TWR real)" if self._responder_client else ""
        )
        self._status_label.setText(f"Listo: INITIATOR{second}")

    def _on_run_clicked(self) -> None:
        if self._initiator_client is None:
            return
        self._model.clear()
        self._run_btn.setEnabled(False)
        self._status_label.setText("Corriendo...")
        self._report_label.setText("")

        worker = ValidationWorker(self._initiator_client, second_client=self._responder_client)
        thread = start_worker(worker)
        worker.check_completed.connect(self._model.add_result)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_finished(self, results: list[ValidationResult], device: DeviceInfo | None) -> None:
        self._run_btn.setEnabled(True)
        counts = summarize(results)
        self._status_label.setText(
            f"{counts['pass']} PASS · {counts['fail']} FAIL · {counts['skip']} SKIP"
        )
        if self._initiator_client is None:
            return
        try:
            json_path, md_path = write_reports(
                results,
                report_dir=Path("reports"),
                port=self._initiator_client.name,
                device=device,
            )
            self._report_label.setText(f"Reportes: {json_path} · {md_path}")
        except Dwm3001cError as exc:
            self._report_label.setText(f"No se pudieron escribir los reportes: {exc}")

    def _on_failed(self, message: str) -> None:
        self._run_btn.setEnabled(True)
        self._status_label.setText(f"Error: {message}")
