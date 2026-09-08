"""Tests de la GUI (fase F9) con pytest-qt, sin hardware ni pantalla real.

Corre con ``QT_QPA_PLATFORM=offscreen`` (ver ``tests/conftest.py``).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMessageBox

import dwm3001c_cli.core.client as client_module
import dwm3001c_cli.gui.views.connection_view as connection_view_module
import dwm3001c_cli.gui.workers as workers_module
import dwm3001c_cli.transport.ble_discovery as ble_discovery_module
import dwm3001c_cli.transport.ble_link as ble_link_module
from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.core.models import ValidationResult
from dwm3001c_cli.gui.main_window import MainWindow
from dwm3001c_cli.gui.models import ValidationResultsModel
from dwm3001c_cli.gui.views.ble_calibration_view import BleCalibrationView
from dwm3001c_cli.gui.views.calibration_view import CalibrationView
from dwm3001c_cli.gui.views.connection_view import ConnectionView
from dwm3001c_cli.gui.views.terminal_view import TerminalView
from dwm3001c_cli.gui.views.validation_view import ValidationView
from dwm3001c_cli.transport.ble_discovery import BleBoardInfo
from dwm3001c_cli.transport.discovery import BoardPort
from fakes import FakeTransport
from test_poll_sampler import PollInitiator, SimResponder, TwrWorld

_LEFT_BUTTON = Qt.MouseButton.LeftButton


@pytest.fixture(autouse=True)
def _drain_qt_events_after_test(qtbot):
    """Deja correr el loop de eventos tras cada test.

    Los workers de ``gui/workers.py`` (``ScanWorker``, ``ConnectWorker``)
    piden ``thread.quit()`` de forma asincrónica al terminar; sin darles
    tiempo a apagarse antes de que ``qtbot`` destruya el widget del test
    siguiente, quedan hilos Qt huérfanos que después cuelgan por completo
    un test no relacionado (visto con ``TestMainWindowWiring``, que necesita
    arrancar su propio ``QThread`` de ``TerminalWorker``).
    """
    yield
    for _ in range(10):
        qtbot.wait(20)


class FakeLink(FakeTransport):
    """``FakeTransport`` con soporte de context manager, como ``SerialLink``."""

    def __init__(self, port: str, script: dict[str, list[str]] | None = None) -> None:
        super().__init__(script=script)
        self._port_name = port

    @property
    def name(self) -> str:
        return self._port_name

    def __enter__(self) -> FakeLink:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def js_stat(app: str = "NONE") -> list[str]:
    return [
        f'JS0080{{"Info":{{"Device":"SIM","Current App":"{app}","Version":"1.1.0",'
        '"Build":"B","Apps":["LISTENER","RESPF","INITF"],"Driver":"D","UWB stack":"S"}}}',
        "ok",
    ]


def basic_script() -> dict[str, list[str]]:
    return {
        "STOP": ["ok"],
        "STAT": js_stat(),
        "DECAID": [
            "Qorvo Device ID = 0xdeca0302",
            "Qorvo Lot ID    = 0x0000505634583230",
            "Qorvo Part ID   = 0x4ef24713",
            "Qorvo SoC ID    = 00005056345832304ef24713",
            "ok",
        ],
        "GETOTP": ["OTP CONTENT: {...}", "ok"],
        "LISTCAL": ["ant0.ch5.ant_delay: 0x00003fed (len: 4)", "ok"],
        "HELP": ["HELP", "STAT", "STOP", "SAVE", "ok"],
        "HELP INITF": ["INITF", "ok"],
        "THREAD": ["hilo 1", "ok"],
        "UART": ["UART: 0", "ok"],
        "DIAG": ["DIAG: 0", "ok"],
        "DIAG 1": ["ok"],
        "DIAG 0": ["ok"],
        "LCFG": ["LCFG", "ok"],
        "CALKEY xtal_trim 0": ["xtal_trim: 0x00 (len: 1)", "ok"],
        "SETAPP NONE": ["ok"],
        "SAVE": ["ok"],
    }


@pytest.fixture
def initiator_client() -> DwmCliClient:
    return DwmCliClient(FakeLink("COM7", basic_script()))


class TestValidationView:
    def test_starts_disabled_until_initiator_connects(
        self, qtbot, initiator_client: DwmCliClient
    ) -> None:
        view = ValidationView()
        qtbot.addWidget(view)

        assert view._run_btn.isEnabled() is False

        view.set_initiator(initiator_client)

        assert view._run_btn.isEnabled() is True


class TestValidationResultsModel:
    @staticmethod
    def _result(command: str, *, passed: bool, detail: str = "ok") -> ValidationResult:
        return ValidationResult(
            command=command,
            sent="X",
            passed=passed,
            detail=detail,
            response_lines=(),
            duration_s=1.0,
        )

    def test_device_column_shows_primary_and_both_for_c4(self, qtbot) -> None:
        model = ValidationResultsModel()
        model.start_run("COM7", "BLE-CCEBFE5BC5E9")
        model.add_result(self._result("A3 STAT", passed=True))
        model.add_result(self._result("C4 Sesión TWR (2 placas)", passed=True))

        assert model.data(model.index(0, 1)) == "COM7"
        assert model.data(model.index(1, 1)) == "COM7 + BLE-CCEBFE5BC5E9"

    def test_status_cell_colors_pass_fail_skip(self, qtbot) -> None:
        model = ValidationResultsModel()
        model.start_run("COM7")
        model.add_result(self._result("A3 STAT", passed=True))
        model.add_result(self._result("A5 DECAID", passed=False, detail="boom"))
        model.add_result(
            self._result("C4 Sesión TWR (2 placas)", passed=True, detail="SKIP: sin segunda placa")
        )

        assert model.data(model.index(0, 2)) == "PASS"
        assert model.data(model.index(1, 2)) == "FAIL"
        assert model.data(model.index(2, 2)) == "SKIP"
        assert model.data(model.index(0, 2), Qt.ItemDataRole.ForegroundRole) == QColor("#1a7f37")
        assert model.data(model.index(1, 2), Qt.ItemDataRole.ForegroundRole) == QColor("#c62828")
        assert model.data(model.index(2, 2), Qt.ItemDataRole.ForegroundRole) == QColor("#6b7280")


class TestConnectionView:
    def test_scan_populates_usb_combo(self, qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
        view = ConnectionView()
        qtbot.addWidget(view)
        boards = [
            BoardPort(
                port="COM7", description="desc", serial_number="SN1", interface_hint="nrf-usb"
            )
        ]
        monkeypatch.setattr(workers_module, "find_boards", lambda: boards)
        # find_ble_boards() hace un scan BLE real de 6s por default (import
        # diferido en ScanWorker.run) — sin mockear, este test golpearía
        # hardware Bluetooth real, violando la regla de "sin hardware".
        monkeypatch.setattr(ble_discovery_module, "find_ble_boards", lambda: [])

        qtbot.mouseClick(view._scan_button, _LEFT_BUTTON)
        qtbot.waitUntil(lambda: view._initiator_port_combo.count() == 1, timeout=2000)

        assert view._initiator_port_combo.itemText(0) == "COM7"
        qtbot.waitUntil(lambda: len(view._active) == 0, timeout=2000)

    def test_connect_initiator_emits_signal_with_working_client(
        self, qtbot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        view = ConnectionView()
        qtbot.addWidget(view)
        view._initiator_port_combo.addItem("COM7")
        link = FakeLink("COM7", basic_script())
        monkeypatch.setattr(connection_view_module, "SerialLink", lambda port: link)

        received: list[object] = []
        view.initiator_connected.connect(lambda transport, client: received.append(client))

        qtbot.mouseClick(view._initiator_connect_btn, _LEFT_BUTTON)
        qtbot.waitUntil(lambda: len(received) == 1, timeout=2000)

        client = received[0]
        assert isinstance(client, DwmCliClient)
        assert client.stat().mode == "NONE"
        qtbot.waitUntil(lambda: len(view._active) == 0, timeout=2000)

    def test_connect_initiator_ble_emits_signal_with_working_client(
        self, qtbot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        view = ConnectionView()
        qtbot.addWidget(view)
        view._initiator_ble_radio.setChecked(True)
        view._initiator_ble_combo.addItem("uwb-01 — ED:7A:8B:7F:35:56", "ED:7A:8B:7F:35:56")
        link = FakeLink("BLE-ED7A8B7F3556", basic_script())
        monkeypatch.setattr(
            connection_view_module, "_connect_ble", lambda address: (link, DwmCliClient(link))
        )

        received: list[object] = []
        view.initiator_connected.connect(lambda transport, client: received.append(client))

        qtbot.mouseClick(view._initiator_connect_btn, _LEFT_BUTTON)
        qtbot.waitUntil(lambda: len(received) == 1, timeout=2000)

        client = received[0]
        assert isinstance(client, DwmCliClient)
        assert client.stat().mode == "NONE"
        assert view._initiator_status.text() == "Conectado: BLE-ED7A8B7F3556"
        qtbot.waitUntil(lambda: len(view._active) == 0, timeout=2000)

    def test_connect_responder_ble_emits_signal_with_working_client(
        self, qtbot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        view = ConnectionView()
        qtbot.addWidget(view)
        view._responder_ble_radio.setChecked(True)
        view._responder_ble_combo.addItem("uwb-02 — FD:7A:90:57:CC:9F", "FD:7A:90:57:CC:9F")
        link = FakeLink("BLE-FD7A9057CC9F", basic_script())
        monkeypatch.setattr(
            connection_view_module, "_connect_ble", lambda address: (link, DwmCliClient(link))
        )

        received: list[object] = []
        view.responder_connected.connect(lambda transport, client: received.append(client))

        qtbot.mouseClick(view._responder_connect_btn, _LEFT_BUTTON)
        qtbot.waitUntil(lambda: len(received) == 1, timeout=2000)

        client = received[0]
        assert isinstance(client, DwmCliClient)
        assert client.stat().mode == "NONE"
        assert view._responder_status.text() == "Conectado: BLE-FD7A9057CC9F"
        qtbot.waitUntil(lambda: len(view._active) == 0, timeout=2000)


class TestMainWindowWiring:
    def test_initiator_connection_enables_other_views(
        self, qtbot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = MainWindow()
        qtbot.addWidget(window)
        connection_view = window._connection_view
        connection_view._initiator_port_combo.addItem("COM7")
        link = FakeLink("COM7", basic_script())
        monkeypatch.setattr(connection_view_module, "SerialLink", lambda port: link)

        qtbot.mouseClick(connection_view._initiator_connect_btn, _LEFT_BUTTON)
        qtbot.waitUntil(lambda: window._validation_view._run_btn.isEnabled(), timeout=2000)

        assert window._calibration_view._initiator_client is not None
        assert window._terminal_view._initiator_transport is link

        # Cierre explícito acá, no solo delegado al teardown automático de
        # qtbot.addWidget(): con un QThread real de TerminalWorker todavía
        # vivo, closeEvent() (que lo detiene) corriendo recién en el teardown
        # de pytest-qt, después de otros tests con hilos propios en la misma
        # sesión, se volvió intermitente sin este cierre explícito dentro del
        # cuerpo del test (mismo camino que en uso real).
        window.close()


class TestTerminalView:
    def test_send_and_receive(self, qtbot) -> None:
        view = TerminalView()
        qtbot.addWidget(view)
        transport = FakeLink("COM7", basic_script())
        view.set_initiator(transport)

        qtbot.waitUntil(lambda: view._send_btn.isEnabled(), timeout=2000)
        view._input.setText("STAT")
        qtbot.mouseClick(view._send_btn, _LEFT_BUTTON)

        qtbot.waitUntil(lambda: "ok" in view._log.toPlainText(), timeout=2000)
        view.stop()


class TestCalibrationView:
    def test_disabled_without_both_clients(self, qtbot, initiator_client: DwmCliClient) -> None:
        view = CalibrationView()
        qtbot.addWidget(view)

        view.set_initiator(initiator_client)

        assert view._run_btn.isEnabled() is False


def ble_devices() -> list[BleBoardInfo]:
    return [
        BleBoardInfo(address="AA:AA:AA:AA:AA:01", name="uwb-01", rssi=-50),
        BleBoardInfo(address="AA:AA:AA:AA:AA:02", name="uwb-02", rssi=-60),
        BleBoardInfo(address="AA:AA:AA:AA:AA:03", name="telefono", rssi=-70),
    ]


def fake_ble_scan(monkeypatch: pytest.MonkeyPatch, devices: list[BleBoardInfo]) -> None:
    """Reemplaza el escaneo BLE real (tocaría adaptador Bluetooth) por una lista fija."""
    monkeypatch.setattr(ble_discovery_module, "find_ble_devices", lambda timeout_s=6.0: devices)


class TestBleCalibrationView:
    def test_scan_lists_all_devices_and_filter_narrows(
        self, qtbot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        view = BleCalibrationView()
        qtbot.addWidget(view)
        fake_ble_scan(monkeypatch, ble_devices())

        assert view._run_btn.isEnabled() is False

        qtbot.mouseClick(view._scan_btn, _LEFT_BUTTON)
        qtbot.waitUntil(lambda: view._device_list.count() == 3, timeout=2000)

        # Preselección: primero INITIATOR, segundo RESPONDER (a calibrar).
        assert view._initiator_combo.count() == 3
        assert view._run_btn.isEnabled() is True
        assert "uwb-02" in view._dut_label.text()

        # El filtro acota la lista Y los combos por substring.
        view._filter_edit.setText("uwb")
        assert view._device_list.count() == 2
        assert view._initiator_combo.count() == 2

        view._filter_edit.setText("telefono")
        assert view._device_list.count() == 1
        assert view._initiator_combo.count() == 1

    def test_run_requires_two_distinct_devices(
        self, qtbot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        view = BleCalibrationView()
        qtbot.addWidget(view)
        fake_ble_scan(monkeypatch, ble_devices())

        qtbot.mouseClick(view._scan_btn, _LEFT_BUTTON)
        qtbot.waitUntil(lambda: view._initiator_combo.count() == 3, timeout=2000)
        assert view._run_btn.isEnabled() is True

        # Mismo dispositivo en ambos roles: no se puede calibrar.
        view._dut_combo.setCurrentIndex(view._initiator_combo.currentIndex())
        assert view._run_btn.isEnabled() is False
        assert "distintos" in view._dut_label.text()

    def test_full_calibration_flow_reports_status_and_live_distance(
        self, qtbot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Física simulada: delay de fábrica 64 unidades bajo el ideal → error
        # ≈ +30 cm; la calibración debe converger al ideal.
        world = TwrWorld(real_cm=200.0, delay=16375, ideal_delay=16439)
        initiator_transport = PollInitiator(world)
        responder_transport = SimResponder(world)
        fake_ble_scan(monkeypatch, ble_devices()[:2])
        # El worker abre los BleTransport dentro de run(): se reemplaza la clase
        # por los transportes falsos según la dirección elegida.
        monkeypatch.setattr(
            ble_link_module,
            "BleTransport",
            lambda address: initiator_transport if address.endswith("01") else responder_transport,
        )
        monkeypatch.setattr(client_module, "_STOP_SETTLE_S", 0.0)
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
        )

        view = BleCalibrationView()
        qtbot.addWidget(view)
        qtbot.mouseClick(view._scan_btn, _LEFT_BUTTON)
        qtbot.waitUntil(lambda: view._run_btn.isEnabled(), timeout=2000)
        view._samples_spin.setValue(10)

        qtbot.mouseClick(view._run_btn, _LEFT_BUTTON)
        qtbot.waitUntil(lambda: "Calibración terminada" in view._status_label.text(), timeout=30000)

        assert "16375" in view._status_label.text()
        assert "16442" in view._status_label.text()  # ideal 16439 ± 5 (tolerancia del bucle)
        assert "Distancia medida:" in view._live_label.text()
        assert "delay 16375 ->" in view._log.toPlainText()
        assert not view.is_running
        assert view._run_btn.isEnabled() is True
