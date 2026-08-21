"""Ventana principal: pestañas Conexión / Terminal / Validar / Calibrar."""

from __future__ import annotations

import logging

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow, QTabWidget

from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.core.errors import Dwm3001cError
from dwm3001c_cli.gui.views.calibration_view import CalibrationView
from dwm3001c_cli.gui.views.connection_view import ConnectionView
from dwm3001c_cli.gui.views.terminal_view import TerminalView
from dwm3001c_cli.gui.views.validation_view import ValidationView
from dwm3001c_cli.transport.serial_link import Transport

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Cablea la vista de conexión con el resto (terminal/validar/calibrar) y
    es la única dueña del ciclo de vida de los transportes conectados — las
    vistas hijas nunca reciben ``closeEvent`` por sí mismas (viven dentro de
    un ``QTabWidget``, no son ventanas de nivel superior).
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("dwm3001c-cli — Panel de control")
        self.resize(900, 700)

        self._initiator_transport: Transport | None = None
        self._responder_transport: Transport | None = None

        self._connection_view = ConnectionView()
        self._terminal_view = TerminalView()
        self._validation_view = ValidationView()
        self._calibration_view = CalibrationView()

        self._connection_view.initiator_connected.connect(self._on_initiator_connected)
        self._connection_view.responder_connected.connect(self._on_responder_connected)

        tabs = QTabWidget()
        tabs.addTab(self._connection_view, "Conexión")
        tabs.addTab(self._terminal_view, "Terminal")
        tabs.addTab(self._validation_view, "Validar")
        tabs.addTab(self._calibration_view, "Calibrar")
        self.setCentralWidget(tabs)

    def _on_initiator_connected(self, transport: Transport, client: DwmCliClient) -> None:
        self._initiator_transport = transport
        self._terminal_view.set_initiator(transport)
        self._validation_view.set_initiator(client)
        self._calibration_view.set_initiator(client)

    def _on_responder_connected(self, transport: Transport, client: DwmCliClient) -> None:
        self._responder_transport = transport
        self._terminal_view.set_responder(transport)
        self._validation_view.set_responder(client)
        self._calibration_view.set_responder(client)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._terminal_view.stop()
        for transport in (self._initiator_transport, self._responder_transport):
            if transport is None:
                continue
            try:
                transport.close()
            except Dwm3001cError:
                logger.exception("Error cerrando un transporte al salir")
        super().closeEvent(event)
