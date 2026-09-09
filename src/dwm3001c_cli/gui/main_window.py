"""Ventana principal: pestañas Calibración BLE / Medir.

[2026-09-09, pedido explícito del usuario] Esta rama (``hardware/ble-bridge-
nrf52840``) usa la GUI solo para el flujo BLE con los puentes nRF52840 — las
pestañas USB (Conexión/Terminal/Validar/Calibrar) generaban confusión al
convivir con las BLE. Se sacaron de acá, pero sus vistas (``connection_view``,
``terminal_view``, ``validation_view``, ``calibration_view``) y sus tests
siguen intactos: esta rama se sincroniza periódicamente trayendo cambios de
``main`` (ver ``docs/rama-hardware-ble.md``), que sí las usa, y borrar el
código directamente generaría conflictos raros en el próximo merge.
"""

from __future__ import annotations

import logging

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow, QTabWidget

from dwm3001c_cli.gui.views.ble_calibration_view import BleCalibrationView
from dwm3001c_cli.gui.views.measure_view import MeasureView

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Ventana de nivel superior: solo las dos pestañas BLE (ver docstring del
    módulo). Cada vista es autónoma — abre y cierra sus propios transportes
    BLE dentro de su worker (que los cierra siempre, incluso ante error) — así
    que esta ventana no es dueña de ningún transporte.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("dwm3001c-cli — Panel de control BLE")
        self.resize(900, 700)

        self._ble_calibration_view = BleCalibrationView()
        self._measure_view = MeasureView()

        tabs = QTabWidget()
        tabs.addTab(self._ble_calibration_view, "Calibración BLE")
        tabs.addTab(self._measure_view, "Medir")
        self.setCentralWidget(tabs)

    def closeEvent(self, event: QCloseEvent) -> None:
        # Los workers cierran sus propios transportes BLE al terminar
        # (finally); si el usuario cierra la ventana a mitad de una
        # calibración/medición, se le avisa en el log: la ventana se cierra
        # igual y el QThread muere con el proceso, sin dejar transportes
        # colgados.
        if self._ble_calibration_view.is_running:
            logger.warning(
                "Cierre con una calibración BLE en curso: los transportes se "
                "cerrarán al terminar el worker."
            )
        if self._measure_view.is_running:
            logger.warning(
                "Cierre con una medición BLE en curso: los transportes se "
                "cerrarán al terminar el worker."
            )
        super().closeEvent(event)
