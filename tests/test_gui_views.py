"""Tests de la GUI (fase F9) con pytest-qt, sin hardware ni pantalla real.

Corre con ``QT_QPA_PLATFORM=offscreen`` (ver ``tests/conftest.py``).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

import dwm3001c_cli.gui.views.connection_view as connection_view_module
import dwm3001c_cli.transport.discovery as discovery_module
from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.gui.main_window import MainWindow
from dwm3001c_cli.gui.views.calibration_view import CalibrationView
from dwm3001c_cli.gui.views.connection_view import ConnectionView
from dwm3001c_cli.gui.views.terminal_view import TerminalView
from dwm3001c_cli.gui.views.validation_view import ValidationView
from dwm3001c_cli.transport.discovery import BoardPort
from fakes import FakeTransport

_LEFT_BUTTON = Qt.MouseButton.LeftButton


class FakeLink(FakeTransport):
    """``FakeTransport`` con soporte de context manager, como ``SerialLink``."""

    def __init__(self, port: str, script: dict[str, list[str]] | None = None) -> None:
        super().__init__(script=script)
        self._port_name = port

    @property
    def name(self) -> str:
        return self._port_name

    def __enter__(self) -> "FakeLink":
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


class TestConnectionView:
    def test_scan_populates_usb_combo(self, qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
        view = ConnectionView()
        qtbot.addWidget(view)
        boards = [
            BoardPort(port="COM7", description="desc", serial_number="SN1", interface_hint="nrf-usb")
        ]
        monkeypatch.setattr(discovery_module, "find_boards", lambda: boards)

        qtbot.mouseClick(view._scan_button, _LEFT_BUTTON)
        qtbot.waitUntil(lambda: view._initiator_port_combo.count() == 1, timeout=2000)

        assert view._initiator_port_combo.itemText(0) == "COM7"

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
