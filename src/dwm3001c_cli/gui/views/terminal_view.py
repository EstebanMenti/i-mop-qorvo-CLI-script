"""Terminal manual: envío/recepción cruda contra la placa INITIATOR o RESPONDER activa."""

from __future__ import annotations

import contextlib
from datetime import datetime

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dwm3001c_cli.gui.workers import TerminalWorker, start_worker
from dwm3001c_cli.transport.serial_link import Transport

_PLACEHOLDER = "(elegí una placa conectada)"


class TerminalView(QWidget):
    """Terminal interactivo crudo, análogo a ``dwm terminal`` pero en la GUI."""

    send_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._initiator_transport: Transport | None = None
        self._responder_transport: Transport | None = None
        self._thread: QThread | None = None
        self._worker: TerminalWorker | None = None

        layout = QVBoxLayout(self)

        target_row = QHBoxLayout()
        self._target_combo = QComboBox()
        self._target_combo.addItem(_PLACEHOLDER, None)
        self._target_combo.currentIndexChanged.connect(self._on_target_changed)
        target_row.addWidget(QLabel("Placa:"))
        target_row.addWidget(self._target_combo, 1)
        layout.addLayout(target_row)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log, 1)

        input_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("Comando crudo, p. ej. STAT")
        self._input.returnPressed.connect(self._on_send)
        self._send_btn = QPushButton("Enviar")
        self._send_btn.clicked.connect(self._on_send)
        self._send_btn.setEnabled(False)
        input_row.addWidget(self._input, 1)
        input_row.addWidget(self._send_btn)
        layout.addLayout(input_row)

    # -------------------------------------------------------- conexión externa

    def set_initiator(self, transport: Transport) -> None:
        self._initiator_transport = transport
        self._refresh_targets(prefer="initiator")

    def set_responder(self, transport: Transport) -> None:
        self._responder_transport = transport
        self._refresh_targets(prefer="responder")

    def _refresh_targets(self, prefer: str | None = None) -> None:
        current = self._target_combo.currentData()
        self._target_combo.blockSignals(True)
        self._target_combo.clear()
        self._target_combo.addItem(_PLACEHOLDER, None)
        if self._initiator_transport is not None:
            self._target_combo.addItem(f"INITIATOR ({self._initiator_transport.name})", "initiator")
        if self._responder_transport is not None:
            self._target_combo.addItem(f"RESPONDER ({self._responder_transport.name})", "responder")
        # Si ya había una selección explícita del usuario, se preserva; si no
        # (todavía en el placeholder), se prioriza el rol recién conectado —
        # si no, conectar una placa no activaba el terminal automáticamente.
        wanted = current if current is not None else prefer
        index = self._target_combo.findData(wanted)
        self._target_combo.setCurrentIndex(index if index >= 0 else 0)
        self._target_combo.blockSignals(False)
        # blockSignals silenció currentIndexChanged durante la reconstrucción
        # del combo: hay que sincronizar el worker con el estado final. Se
        # difiere con singleShot(0) en vez de llamar directo — set_initiator/
        # set_responder pueden llegar aquí desde varios niveles de señales
        # anidadas (ConnectWorker en otro hilo -> señal encolada -> señal
        # directa de MainWindow), y arrancar un QThread nuevo de forma
        # síncrona en medio de esa cadena colgaba la app (visto en tests:
        # TestMainWindowWiring se cuelga sin este defer). Diferir al próximo
        # tick del loop de eventos rompe la anidación y es el patrón estándar
        # de Qt para esto.
        QTimer.singleShot(0, self._on_target_changed)

    # ------------------------------------------------------------ worker

    def _on_target_changed(self) -> None:
        self._stop_worker()
        role = self._target_combo.currentData()
        transport = (
            self._initiator_transport
            if role == "initiator"
            else self._responder_transport
            if role == "responder"
            else None
        )
        if transport is None:
            self._send_btn.setEnabled(False)
            return
        self._start_worker(transport)

    def _start_worker(self, transport: Transport) -> None:
        worker = TerminalWorker(transport)
        thread = start_worker(worker)
        worker.line_received.connect(self._on_line_received)
        self.send_requested.connect(worker.send)
        self._worker = worker
        self._thread = thread
        self._send_btn.setEnabled(True)
        thread.start()

    def _stop_worker(self) -> None:
        if self._worker is not None:
            with contextlib.suppress(RuntimeError, TypeError):
                self.send_requested.disconnect()
        if self._thread is not None:
            # thread.quit() alcanza y no bloquea: para el loop de eventos del
            # hilo, y sin loop de eventos el QTimer de TerminalWorker deja de
            # disparar solo (no hace falta pedirle al worker que se detenga a
            # mano). No usar wait(): un join bloqueante acá (p. ej. desde
            # closeEvent) puede colgar la ventana entera — confirmado con
            # hardware simulado (FakeTransport en tests). thread.finished ya
            # está conectado a deleteLater() en start_worker(), así que el
            # QThread se limpia solo.
            self._thread.quit()
        self._worker = None
        self._thread = None
        self._send_btn.setEnabled(False)

    def stop(self) -> None:
        """Detiene el worker activo (llamar al cerrar la ventana)."""
        self._stop_worker()

    # -------------------------------------------------------------- envío

    def _on_send(self) -> None:
        text = self._input.text().strip()
        if not text or self._worker is None:
            return
        self._append_log(f"> {text}")
        self.send_requested.emit(text)
        self._input.clear()

    def _on_line_received(self, line: str) -> None:
        self._append_log(line)

    def _append_log(self, text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log.appendPlainText(f"[{timestamp}] {text}")
