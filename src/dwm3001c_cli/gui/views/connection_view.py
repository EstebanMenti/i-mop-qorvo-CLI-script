"""Vista de conexión: INITIATOR y RESPONDER, cada uno por USB o por BLE.

Cada rol se conecta por puerto serie USB (placa directa) o por Bluetooth
(puente nRF52840, rama ``hardware/ble-bridge-nrf52840``), de forma
independiente — por ejemplo, ambos por BLE para la calibración remota.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.gui.workers import (
    BoardPort,
    CallableWorker,
    ConnectWorker,
    ScanWorker,
    format_ble_device_status,
    start_worker,
)
from dwm3001c_cli.transport.serial_link import SerialLink, Transport

# [Verificado 2026-08-13, hardware real] Mismo valor que _BLE_QUIET_PERIOD_S
# en app/cli.py — el default de DwmCliClient (0.3s, calibrado para USB) corta
# la lectura a mitad de respuesta por BLE.
#
# [Bug real, 2026-09-09, hardware real] 10.0s no alcanza para INITF: al
# arrancar la sesión, sus SESSION_INFO_NTF compiten por el mismo canal de
# comandos con el eco/"ok" de INITF durante una ventana de transición —
# confirmado tardando hasta ~10.4s en una corrida real (ver comentario
# equivalente en BlePairCalibrationWorker, gui/workers.py).
_BLE_QUIET_PERIOD_S = 1.5
_BLE_COMMAND_TIMEOUT_S = 20.0


def _connect_usb(port: str) -> tuple[Transport, DwmCliClient]:
    link = SerialLink(port)
    link.open()
    return link, DwmCliClient(link)


def _connect_ble(address: str) -> tuple[Transport, DwmCliClient]:
    from dwm3001c_cli.transport.ble_link import BleTransport

    link = BleTransport(address)
    link.open()
    client = DwmCliClient(
        link, command_timeout_s=_BLE_COMMAND_TIMEOUT_S, quiet_period_s=_BLE_QUIET_PERIOD_S
    )
    return link, client


def _fetch_ble_device_status(transport: object) -> tuple[int | None, str | None]:
    """Lee batería y versión de firmware del puente BLE ya conectado.

    [Mitigación 2026-09-09, hardware real] Antes se leía automáticamente
    apenas se conectaba cada nodo, en ``_connect_ble()`` — sacado de ahí
    porque, con hardware real, esa lectura en el mismo momento en que el
    OTRO nodo podía estar terminando de conectar (dos hilos de
    ``BleTransport`` distintos, cada uno con su propia actividad WinRT
    nativa) coincidía con el crash nativo ya documentado en
    ``docs/rama-hardware-ble.md`` §8 (confirmado con un volcado real,
    ``STATUS_STACK_BUFFER_OVERRUN``/``0xC0000409``) y lo hacía mucho más
    frecuente. Ahora es una acción manual (botón "Ver info"): el usuario la
    dispara un nodo a la vez, ya con las conexiones estables.
    """
    return transport.read_battery_level(), transport.read_bridge_firmware_version()  # type: ignore[attr-defined]


class ConnectionView(QWidget):
    """Escanea y conecta las placas INITIATOR/RESPONDER; expone los clientes ya listos."""

    initiator_connected = Signal(object, object)  # (Transport, DwmCliClient)
    responder_connected = Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        # No es dueño del ciclo de vida de los transportes conectados: los
        # emite por señal y el dueño real (MainWindow, ventana de nivel
        # superior) es responsable de cerrarlos — este widget vive dentro de
        # un QTabWidget y nunca recibe closeEvent por sí mismo. Sí guarda una
        # referencia de solo lectura por rol, para que el botón "Ver info"
        # (batería/firmware, ver _fetch_ble_device_status) tenga sobre qué
        # transporte operar sin depender de que el resto de la GUI se la
        # pase de vuelta.
        self._initiator_transport: Transport | None = None
        self._responder_transport: Transport | None = None
        self._active: list[tuple[QThread, object]] = []  # mantiene vivos threads+workers en curso

        layout = QVBoxLayout(self)

        scan_row = QHBoxLayout()
        self._scan_button = QPushButton("Escanear placas (USB + BLE)")
        self._scan_button.clicked.connect(self._on_scan_clicked)
        self._scan_status = QLabel("Sin escanear todavía.")
        scan_row.addWidget(self._scan_button)
        scan_row.addWidget(self._scan_status, 1)
        layout.addLayout(scan_row)

        layout.addWidget(self._build_initiator_box())
        layout.addWidget(self._build_responder_box())
        layout.addStretch(1)

    def _build_initiator_box(self) -> QGroupBox:
        box = QGroupBox("INITIATOR")
        outer = QVBoxLayout(box)

        radio_row = QHBoxLayout()
        self._initiator_usb_radio = QRadioButton("USB")
        self._initiator_ble_radio = QRadioButton("Bluetooth (puente nRF52840)")
        self._initiator_usb_radio.setChecked(True)
        self._initiator_usb_radio.toggled.connect(self._on_initiator_mode_toggled)
        radio_row.addWidget(self._initiator_usb_radio)
        radio_row.addWidget(self._initiator_ble_radio)
        radio_row.addStretch(1)
        outer.addLayout(radio_row)

        row = QHBoxLayout()
        self._initiator_port_combo = QComboBox()
        self._initiator_ble_combo = QComboBox()
        self._initiator_ble_combo.setEnabled(False)
        self._initiator_connect_btn = QPushButton("Conectar")
        self._initiator_connect_btn.clicked.connect(self._connect_initiator)
        self._initiator_status = QLabel("Desconectado")
        row.addWidget(QLabel("Puerto/dirección:"))
        row.addWidget(self._initiator_port_combo, 1)
        row.addWidget(self._initiator_ble_combo, 1)
        row.addWidget(self._initiator_connect_btn)
        row.addWidget(self._initiator_status)
        outer.addLayout(row)

        info_row = QHBoxLayout()
        self._initiator_info_btn = QPushButton("Ver info (batería/firmware)")
        self._initiator_info_btn.clicked.connect(self._fetch_initiator_info)
        self._initiator_info_btn.hide()
        self._initiator_device_label = QLabel("")
        self._initiator_device_label.setStyleSheet("color: #495057; font-style: italic;")
        info_row.addWidget(self._initiator_info_btn)
        info_row.addWidget(self._initiator_device_label, 1)
        outer.addLayout(info_row)
        return box

    def _on_initiator_mode_toggled(self) -> None:
        is_ble = self._initiator_ble_radio.isChecked()
        self._initiator_port_combo.setEnabled(not is_ble)
        self._initiator_ble_combo.setEnabled(is_ble)

    def _build_responder_box(self) -> QGroupBox:
        box = QGroupBox("RESPONDER")
        outer = QVBoxLayout(box)

        radio_row = QHBoxLayout()
        self._responder_usb_radio = QRadioButton("USB")
        self._responder_ble_radio = QRadioButton("Bluetooth (puente nRF52840)")
        self._responder_usb_radio.setChecked(True)
        self._responder_usb_radio.toggled.connect(self._on_responder_mode_toggled)
        radio_row.addWidget(self._responder_usb_radio)
        radio_row.addWidget(self._responder_ble_radio)
        radio_row.addStretch(1)
        outer.addLayout(radio_row)

        row = QHBoxLayout()
        self._responder_usb_combo = QComboBox()
        self._responder_ble_combo = QComboBox()
        self._responder_ble_combo.setEnabled(False)
        self._responder_connect_btn = QPushButton("Conectar")
        self._responder_connect_btn.clicked.connect(self._connect_responder)
        self._responder_status = QLabel("Desconectado")
        row.addWidget(QLabel("Puerto/dirección:"))
        row.addWidget(self._responder_usb_combo, 1)
        row.addWidget(self._responder_ble_combo, 1)
        row.addWidget(self._responder_connect_btn)
        row.addWidget(self._responder_status)
        outer.addLayout(row)

        info_row = QHBoxLayout()
        self._responder_info_btn = QPushButton("Ver info (batería/firmware)")
        self._responder_info_btn.clicked.connect(self._fetch_responder_info)
        self._responder_info_btn.hide()
        self._responder_device_label = QLabel("")
        self._responder_device_label.setStyleSheet("color: #495057; font-style: italic;")
        info_row.addWidget(self._responder_info_btn)
        info_row.addWidget(self._responder_device_label, 1)
        outer.addLayout(info_row)
        return box

    def _on_responder_mode_toggled(self) -> None:
        is_ble = self._responder_ble_radio.isChecked()
        self._responder_usb_combo.setEnabled(not is_ble)
        self._responder_ble_combo.setEnabled(is_ble)

    # ------------------------------------------------------------- escaneo

    def _on_scan_clicked(self) -> None:
        self._scan_button.setEnabled(False)
        self._scan_status.setText("Escaneando (el escaneo BLE puede tardar varios segundos)...")
        worker = ScanWorker()
        thread = start_worker(worker)
        worker.finished.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._keep_alive(thread, worker)
        thread.start()

    def _on_scan_finished(self, usb_boards: list[BoardPort], ble_boards: list[object]) -> None:
        self._scan_button.setEnabled(True)
        self._initiator_port_combo.clear()
        self._responder_usb_combo.clear()
        for board in usb_boards:
            self._initiator_port_combo.addItem(board.port)
            self._responder_usb_combo.addItem(board.port)
        self._initiator_ble_combo.clear()
        self._responder_ble_combo.clear()
        for ble_board in ble_boards:
            name = getattr(ble_board, "name", "") or "(sin nombre)"
            address = getattr(ble_board, "address", "")
            self._initiator_ble_combo.addItem(f"{name} — {address}", address)
            self._responder_ble_combo.addItem(f"{name} — {address}", address)
        self._scan_status.setText(
            f"{len(usb_boards)} placa(s) USB, {len(ble_boards)} puente(s) BLE encontrados."
        )

    def _on_scan_failed(self, message: str) -> None:
        self._scan_button.setEnabled(True)
        self._scan_status.setText(f"Error al escanear: {message}")

    # -------------------------------------------------------- INITIATOR

    def _connect_initiator(self) -> None:
        if self._initiator_ble_radio.isChecked():
            address = self._initiator_ble_combo.currentData()
            if not address:
                self._initiator_status.setText("Elegí un puente BLE primero (o escaneá).")
                return
            worker = ConnectWorker(lambda: _connect_ble(address))
        else:
            port = self._initiator_port_combo.currentText()
            if not port:
                self._initiator_status.setText("Elegí un puerto primero.")
                return
            worker = ConnectWorker(lambda: _connect_usb(port))

        self._initiator_connect_btn.setEnabled(False)
        self._initiator_status.setText("Conectando...")
        self._initiator_info_btn.hide()
        self._initiator_device_label.setText("")
        thread = start_worker(worker)
        worker.connected.connect(self._on_initiator_connected)
        worker.failed.connect(self._on_initiator_connect_failed)
        worker.connected.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._keep_alive(thread, worker)
        thread.start()

    def _on_initiator_connected(self, transport: Transport, client: DwmCliClient) -> None:
        from dwm3001c_cli.transport.ble_link import BleTransport

        self._initiator_connect_btn.setEnabled(True)
        self._initiator_status.setText(f"Conectado: {client.name}")
        self._initiator_transport = transport
        self._initiator_info_btn.setVisible(isinstance(transport, BleTransport))
        self.initiator_connected.emit(transport, client)

    def _on_initiator_connect_failed(self, message: str) -> None:
        self._initiator_connect_btn.setEnabled(True)
        self._initiator_status.setText(f"Error: {message}")

    def _fetch_initiator_info(self) -> None:
        transport = self._initiator_transport
        if transport is None:
            return
        self._initiator_info_btn.setEnabled(False)
        self._initiator_device_label.setText("Consultando…")
        worker = CallableWorker(lambda: _fetch_ble_device_status(transport))
        thread = start_worker(worker)
        worker.finished.connect(self._on_initiator_info_result)
        worker.failed.connect(self._on_initiator_info_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._keep_alive(thread, worker)
        thread.start()

    def _on_initiator_info_result(self, result: tuple[int | None, str | None]) -> None:
        self._initiator_info_btn.setEnabled(True)
        battery, firmware = result
        self._initiator_device_label.setText(format_ble_device_status(battery, firmware))

    def _on_initiator_info_failed(self, message: str) -> None:
        self._initiator_info_btn.setEnabled(True)
        self._initiator_device_label.setText(f"No se pudo consultar: {message}")

    # -------------------------------------------------------- RESPONDER

    def _connect_responder(self) -> None:
        if self._responder_ble_radio.isChecked():
            address = self._responder_ble_combo.currentData()
            if not address:
                self._responder_status.setText("Elegí un puente BLE primero (o escaneá).")
                return
            worker = ConnectWorker(lambda: _connect_ble(address))
        else:
            port = self._responder_usb_combo.currentText()
            if not port:
                self._responder_status.setText("Elegí un puerto primero.")
                return
            worker = ConnectWorker(lambda: _connect_usb(port))

        self._responder_connect_btn.setEnabled(False)
        self._responder_status.setText("Conectando...")
        self._responder_info_btn.hide()
        self._responder_device_label.setText("")
        thread = start_worker(worker)
        worker.connected.connect(self._on_responder_connected)
        worker.failed.connect(self._on_responder_connect_failed)
        worker.connected.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._keep_alive(thread, worker)
        thread.start()

    def _on_responder_connected(self, transport: Transport, client: DwmCliClient) -> None:
        from dwm3001c_cli.transport.ble_link import BleTransport

        self._responder_connect_btn.setEnabled(True)
        self._responder_status.setText(f"Conectado: {client.name}")
        self._responder_transport = transport
        self._responder_info_btn.setVisible(isinstance(transport, BleTransport))
        self.responder_connected.emit(transport, client)

    def _on_responder_connect_failed(self, message: str) -> None:
        self._responder_connect_btn.setEnabled(True)
        self._responder_status.setText(f"Error: {message}")

    def _fetch_responder_info(self) -> None:
        transport = self._responder_transport
        if transport is None:
            return
        self._responder_info_btn.setEnabled(False)
        self._responder_device_label.setText("Consultando…")
        worker = CallableWorker(lambda: _fetch_ble_device_status(transport))
        thread = start_worker(worker)
        worker.finished.connect(self._on_responder_info_result)
        worker.failed.connect(self._on_responder_info_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._keep_alive(thread, worker)
        thread.start()

    def _on_responder_info_result(self, result: tuple[int | None, str | None]) -> None:
        self._responder_info_btn.setEnabled(True)
        battery, firmware = result
        self._responder_device_label.setText(format_ble_device_status(battery, firmware))

    def _on_responder_info_failed(self, message: str) -> None:
        self._responder_info_btn.setEnabled(True)
        self._responder_device_label.setText(f"No se pudo consultar: {message}")

    # ---------------------------------------------------------------- utils

    def _keep_alive(self, thread: QThread, worker: object) -> None:
        entry = (thread, worker)
        self._active.append(entry)
        thread.finished.connect(
            lambda: self._active.remove(entry) if entry in self._active else None
        )
