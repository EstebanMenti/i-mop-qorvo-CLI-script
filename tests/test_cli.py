"""Tests de la aplicación de consola ``dwm`` (fase F5), con transporte simulado."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import dwm3001c_cli.app.cli as cli_module
from dwm3001c_cli.calibration.autocal import CalibrationReport
from dwm3001c_cli.core.models import ValidationResult
from dwm3001c_cli.transport.discovery import BoardPort
from fakes import FakeTransport

runner = CliRunner()


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


DECAID_LINES = [
    "Qorvo Device ID = 0xdeca0302",
    "Qorvo Lot ID    = 0x0000505634583230",
    "Qorvo Part ID   = 0x4ef24713",
    "Qorvo SoC ID    = 00005056345832304ef24713",
    "ok",
]

LISTCAL_LINES = [
    "ant0.ch5.ant_delay: 0x00003fed (len: 4)",
    "ant0.ch9.ant_delay: 0x00003ff7 (len: 4)",
    "ok",
]


def basic_script() -> dict[str, list[str]]:
    return {
        "STOP": ["ok"],
        "STAT": js_stat(),
        "DECAID": DECAID_LINES,
        "GETOTP": ["OTP CONTENT: {...}", "ok"],
        "LISTCAL": LISTCAL_LINES,
    }


def patch_links(monkeypatch: pytest.MonkeyPatch, links: dict[str, FakeLink]) -> None:
    monkeypatch.setattr(cli_module, "SerialLink", lambda port: links[port])


class TestPorts:
    def test_without_boards_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli_module, "find_boards", lambda: [])

        result = runner.invoke(cli_module.app, ["ports"])

        assert result.exit_code == 1
        assert "No se detectó" in result.stdout

    def test_with_boards_lists_them(self, monkeypatch: pytest.MonkeyPatch) -> None:
        boards = [
            BoardPort(
                port="COM7", description="desc", serial_number="SN1", interface_hint="nrf-usb"
            )
        ]
        monkeypatch.setattr(cli_module, "find_boards", lambda: boards)

        result = runner.invoke(cli_module.app, ["ports"])

        assert result.exit_code == 0
        assert "COM7" in result.stdout
        assert "SN1" in result.stdout


class TestInfo:
    def test_missing_port_exits_2(self) -> None:
        result = runner.invoke(cli_module.app, ["info"])

        assert result.exit_code == 2

    def test_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_links(monkeypatch, {"COM7": FakeLink("COM7", basic_script())})

        result = runner.invoke(cli_module.app, ["info", "--port", "COM7"])

        assert result.exit_code == 0
        assert "0xdeca0302" in result.stdout
        assert "ant0.ch9.ant_delay" in result.stdout


class TestBleProvision:
    def test_missing_port_exits_2(self) -> None:
        result = runner.invoke(cli_module.app, ["ble-provision"])

        assert result.exit_code == 2

    def test_declined_confirmation_does_not_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        link = FakeLink("COM7", basic_script())
        link.queue_response("UART", ["UART: 0"])
        patch_links(monkeypatch, {"COM7": link})

        result = runner.invoke(cli_module.app, ["ble-provision", "--port", "COM7"], input="n\n")

        assert result.exit_code == 1
        assert "cancelado" in result.stdout
        assert "UART 1" not in link.sent

    def test_yes_skips_confirmation_and_enables_uart(self, monkeypatch: pytest.MonkeyPatch) -> None:
        link = FakeLink("COM7", basic_script())
        link.queue_response("UART", ["UART: 0"])
        link.queue_response("UART 1", ["ok"])
        link.queue_response("SAVE", ["ok"])
        link.queue_response("UART", ["UART: 1"])
        patch_links(monkeypatch, {"COM7": link})

        result = runner.invoke(cli_module.app, ["ble-provision", "--port", "COM7", "--yes"])

        assert result.exit_code == 0
        assert link.sent == ["STOP", "STAT", "UART", "UART 1", "SAVE", "UART"]

    def test_uart_1_rejected_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        link = FakeLink("COM7", basic_script())
        link.queue_response("UART", ["UART: 0"])
        link.queue_response("UART 1", ["KO"])
        patch_links(monkeypatch, {"COM7": link})

        result = runner.invoke(cli_module.app, ["ble-provision", "--port", "COM7", "--yes"])

        assert result.exit_code == 1


class TestValidate:
    def test_missing_port_exits_2(self) -> None:
        result = runner.invoke(cli_module.app, ["validate"])

        assert result.exit_code == 2

    def test_writes_reports_and_exits_0_when_all_pass(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        patch_links(monkeypatch, {"COM7": FakeLink("COM7", basic_script())})
        results = [
            ValidationResult(
                command="A1 HELP",
                sent="HELP",
                passed=True,
                detail="ok",
                response_lines=("ok",),
                duration_s=0.1,
            )
        ]
        monkeypatch.setattr(cli_module, "run_validation", lambda client, **kw: results)

        result = runner.invoke(
            cli_module.app, ["validate", "--port", "COM7", "--report-dir", str(tmp_path)]
        )

        assert result.exit_code == 0
        json_files = list(tmp_path.glob("validacion-COM7-*.json"))
        md_files = list(tmp_path.glob("validacion-COM7-*.md"))
        assert len(json_files) == 1
        assert len(md_files) == 1

    def test_exit_code_1_on_failure(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        patch_links(monkeypatch, {"COM7": FakeLink("COM7", basic_script())})
        results = [
            ValidationResult(
                command="A5 DECAID",
                sent="DECAID",
                passed=False,
                detail="mal",
                response_lines=(),
                duration_s=0.1,
            )
        ]
        monkeypatch.setattr(cli_module, "run_validation", lambda client, **kw: results)

        result = runner.invoke(
            cli_module.app, ["validate", "--port", "COM7", "--report-dir", str(tmp_path)]
        )

        assert result.exit_code == 1

    def test_passes_second_client_when_second_port_given(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        patch_links(
            monkeypatch,
            {"COM7": FakeLink("COM7", basic_script()), "COM8": FakeLink("COM8", basic_script())},
        )
        captured: dict[str, object] = {}

        def fake_run_validation(
            client: object, *, second_client: object = None, **kw: object
        ) -> list[ValidationResult]:
            captured["second_client"] = second_client
            return []

        monkeypatch.setattr(cli_module, "run_validation", fake_run_validation)

        result = runner.invoke(
            cli_module.app,
            ["validate", "--port", "COM7", "--second-port", "COM8", "--report-dir", str(tmp_path)],
        )

        assert result.exit_code == 0
        assert captured["second_client"] is not None

    def test_config_precedence_cli_wins_over_yaml(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        patch_links(
            monkeypatch,
            {
                "COM_YAML": FakeLink("COM_YAML", basic_script()),
                "COM_CLI": FakeLink("COM_CLI", basic_script()),
            },
        )
        monkeypatch.setattr(cli_module, "run_validation", lambda client, **kw: [])
        config_path = tmp_path / "cfg.yaml"
        config_path.write_text("port: COM_YAML\n", encoding="utf-8")

        # Sin --port explícito: debe usar el del YAML.
        result_yaml = runner.invoke(
            cli_module.app,
            ["validate", "--config", str(config_path), "--report-dir", str(tmp_path)],
        )
        assert result_yaml.exit_code == 0
        assert list(tmp_path.glob("validacion-COM_YAML-*.json"))

        # Con --port explícito: la CLI gana sobre el YAML.
        result_cli = runner.invoke(
            cli_module.app,
            [
                "validate",
                "--config",
                str(config_path),
                "--port",
                "COM_CLI",
                "--report-dir",
                str(tmp_path),
            ],
        )
        assert result_cli.exit_code == 0
        assert list(tmp_path.glob("validacion-COM_CLI-*.json"))

    def test_unknown_yaml_key_fails(self, tmp_path: Path) -> None:
        config_path = tmp_path / "cfg.yaml"
        config_path.write_text("port: COM7\nrestore: true\n", encoding="utf-8")

        result = runner.invoke(cli_module.app, ["validate", "--config", str(config_path)])

        assert result.exit_code != 0


class TestCalibrate:
    def test_missing_required_options_exits_2(self) -> None:
        result = runner.invoke(cli_module.app, ["calibrate"])

        assert result.exit_code == 2

    def _canned_report(self) -> CalibrationReport:
        return CalibrationReport(
            device_port="COM8",
            reference_port="COM7",
            key="ant0.ch9.ant_delay",
            real_distance_cm=200.0,
            initial_delay=16375,
            final_delay=16439,
            sensitivity_cm_per_unit=0.57,
            iterations=(),
            converged=True,
            saved=True,
        )

    def _calibrate_script(self) -> dict[str, list[str]]:
        script = basic_script()
        script["CALKEY ant0.ch9.ant_delay"] = [
            "ant0.ch9.ant_delay: 0x00003ff7 (len: 4)",
            "ok",
        ]
        return script

    def test_declined_confirmation_cancels_without_calling_autocalibrate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_links(
            monkeypatch,
            {
                "COM7": FakeLink("COM7", self._calibrate_script()),
                "COM8": FakeLink("COM8", self._calibrate_script()),
            },
        )
        called = False

        def fake_autocalibrate(*args: object, **kwargs: object) -> CalibrationReport:
            nonlocal called
            called = True
            return self._canned_report()

        monkeypatch.setattr(cli_module, "autocalibrate", fake_autocalibrate)

        result = runner.invoke(
            cli_module.app,
            [
                "calibrate",
                "--initiator",
                "COM7",
                "--responder",
                "COM8",
                "--distance-m",
                "2.0",
            ],
            input="n\n",
        )

        assert result.exit_code == 1
        assert "cancelada" in result.stdout
        assert called is False

    def test_yes_skips_confirmation_and_calls_autocalibrate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_links(
            monkeypatch,
            {
                "COM7": FakeLink("COM7", self._calibrate_script()),
                "COM8": FakeLink("COM8", self._calibrate_script()),
            },
        )
        calls: list[tuple[object, ...]] = []

        def fake_autocalibrate(
            device: object, reference: object, **kwargs: object
        ) -> CalibrationReport:
            calls.append((device, reference, kwargs))
            return self._canned_report()

        monkeypatch.setattr(cli_module, "autocalibrate", fake_autocalibrate)

        result = runner.invoke(
            cli_module.app,
            [
                "calibrate",
                "--initiator",
                "COM7",
                "--responder",
                "COM8",
                "--distance-m",
                "2.0",
                "--yes",
            ],
        )

        assert result.exit_code == 0
        assert len(calls) == 1
        assert "16439" in result.stdout

    def test_accepted_confirmation_calls_autocalibrate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_links(
            monkeypatch,
            {
                "COM7": FakeLink("COM7", self._calibrate_script()),
                "COM8": FakeLink("COM8", self._calibrate_script()),
            },
        )
        monkeypatch.setattr(cli_module, "autocalibrate", lambda *a, **kw: self._canned_report())

        result = runner.invoke(
            cli_module.app,
            [
                "calibrate",
                "--initiator",
                "COM7",
                "--responder",
                "COM8",
                "--distance-m",
                "2.0",
            ],
            input="y\n",
        )

        assert result.exit_code == 0

    def test_no_save_flag_is_not_overridable_by_yaml(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "cfg.yaml"
        config_path.write_text("no_save: true\n", encoding="utf-8")

        result = runner.invoke(
            cli_module.app,
            [
                "calibrate",
                "--initiator",
                "COM7",
                "--responder",
                "COM8",
                "--distance-m",
                "2.0",
                "--config",
                str(config_path),
            ],
        )

        assert result.exit_code != 0  # clave desconocida: no_save no es configurable por YAML
