"""Tests de la suite de validación de comandos (fase F3)."""

import json
from pathlib import Path

from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.core.models import ValidationResult
from dwm3001c_cli.validation.report import status_of, summarize, write_reports
from dwm3001c_cli.validation.runner import run_validation
from dwm3001c_cli.validation.spec import ALL_CHECKS
from fakes import FakeTransport

FIXTURES = Path(__file__).parent / "fixtures"
LISTCAL_LINES = (FIXTURES / "listcal_manual.txt").read_text(encoding="utf-8").splitlines()


def js_stat(app: str) -> list[str]:
    return [
        f'JS0080{{"Info":{{"Device":"X","Current App":"{app}","Version":"1.1.0",'
        '"Build":"B","Apps":["LISTENER","RESPF","INITF"],"Driver":"D","UWB stack":"S"}}}',
        "ok",
    ]


def ntf_line(n: int) -> str:
    return (
        f"SESSION_INFO_NTF: {{session_handle=1, sequence_number={n}, block_index={n},"
        ' n_measurements=1 [mac_address=0x0001, status="SUCCESS", distance[cm]=199]}'
    )


DECAID_OK = [
    "Qorvo Device ID = 0xdeca0302",
    "Qorvo Lot ID    = 0x0000505634583230",
    "Qorvo Part ID   = 0x4ef24713",
    "Qorvo SoC ID    = 00005056345832304ef24713",
    "ok",
]

SCRIPT_OK: dict[str, list[str]] = {
    "STOP": ["ok"],
    "HELP": ["STAT - report status", "STOP - stop app", "SAVE - save config", "ok"],
    "HELP INITF": ["INITF: FiRa TWR initiator", "ok"],
    "THREAD": ["idle: 100/200", "cli: 50/100", "ok"],
    "DECAID": DECAID_OK,
    "GETOTP": ["0x01A: 0x00004015", "0x01C: 0x00004015", "ok"],
    "LISTCAL": LISTCAL_LINES,
    "CALKEY ant0.ch9.ant_delay": ["ant0.ch9.ant_delay: 0x3FF7 (len: 4)", "ok"],
    # B2 elige la primera clave de LISTCAL con valor <=9: restricted_channels (0).
    "CALKEY restricted_channels 0": ["restricted_channels: 0x0000 (len: 2)", "ok"],
    "CALKEY restricted_channels": ["restricted_channels: 0x0000 (len: 2)", "ok"],
    "UART": ["uart: 0", "ok"],
    "DIAG": ["diag: 1", "ok"],
    "LCFG": ["CHAN: 9", "PCODE: 10", "ok"],
    "DIAG 1": ["ok"],
    "DIAG 0": ["ok"],
    "SETAPP NONE": ["ok"],
    "SAVE": ["ok"],
    "LISTENER": ["ok"],
    "INITF": ["ok"],
    "RESPF": ["ok"],
}


def make_full_pass_client() -> tuple[DwmCliClient, FakeTransport]:
    transport = FakeTransport(script=dict(SCRIPT_OK))
    # STAT cambia con el estado: NONE (runner), NONE (A3), LISTENER (C1),
    # NONE (limpieza C1), INITF (C2), NONE, RESPF (C3), NONE.
    for app in ["NONE", "NONE", "LISTENER", "NONE", "INITF", "NONE", "RESPF", "NONE"]:
        transport.queue_response("STAT", js_stat(app))
    return DwmCliClient(transport, command_timeout_s=0.2), transport


class TestFullSuite:
    def test_all_checks_pass_with_correct_firmware(self) -> None:
        client, transport = make_full_pass_client()

        results = run_validation(client, settle_delay_s=0, ranging_window_s=0.05)

        by_status = {status_of(r) for r in results}
        details = {r.command: r.detail for r in results if status_of(r) == "FAIL"}
        assert details == {}, f"checks fallidos: {details}"
        assert len(results) == len(ALL_CHECKS)
        assert by_status == {"PASS", "SKIP"}  # C4 se saltea sin segunda placa
        assert "DIAG 0" in transport.sent  # limpieza de B1 ejecutada

    def test_failures_do_not_abort_suite_and_cleanup_runs(self) -> None:
        script = dict(SCRIPT_OK)
        script["DECAID"] = ["basura sin sentido", "ok"]  # rompe A5
        script["DIAG 1"] = ["error: not supported"]  # rompe B1
        # STAT estático en NONE: C1/C2/C3 fallarán (la app "no arranca").
        script["STAT"] = js_stat("NONE")
        transport = FakeTransport(script=script)
        client = DwmCliClient(transport, command_timeout_s=0.2)

        results = run_validation(client, settle_delay_s=0, ranging_window_s=0.05)

        assert len(results) == len(ALL_CHECKS)  # ningún fallo abortó la suite
        by_id = {r.command: r for r in results}
        assert not by_id["A5 DECAID"].passed
        assert not by_id["B1 DIAG (toggle)"].passed
        assert not by_id["C1 LISTENER"].passed
        assert by_id["A1 HELP"].passed
        assert "DIAG 0" in transport.sent  # limpieza de B1 pese al fallo

    def test_c4_with_two_boards(self) -> None:
        initiator_transport = FakeTransport(
            script={"STOP": ["ok"], "STAT": js_stat("NONE"), "INITF": ["ok"]},
            notifications=[ntf_line(i) for i in range(3)],
        )
        responder_transport = FakeTransport(
            script={"STOP": ["ok"], "STAT": js_stat("NONE"), "RESPF": ["ok"]}
        )
        initiator = DwmCliClient(initiator_transport, command_timeout_s=0.2)
        responder = DwmCliClient(responder_transport, command_timeout_s=0.2)

        results = run_validation(
            initiator,
            second_client=responder,
            settle_delay_s=0,
            ranging_window_s=0.1,
            check_ids={"C4"},
        )

        assert len(results) == 1
        assert results[0].passed
        assert "3/3" in results[0].detail
        # Mismo SessionParams que dwm calibrate: ADDR (ID propio) y PADDR (ID
        # del par) explícitos por rol, no defaults del firmware.
        respf_sent = next(cmd for cmd in responder_transport.sent if cmd.startswith("RESPF"))
        initf_sent = next(cmd for cmd in initiator_transport.sent if cmd.startswith("INITF"))
        assert "-ADDR=1" in respf_sent
        assert "-PADDR=0" in respf_sent
        assert "-ADDR=0" in initf_sent
        assert "-PADDR=1" in initf_sent

    def test_check_ids_filters_suite(self) -> None:
        client, _ = make_full_pass_client()

        results = run_validation(client, settle_delay_s=0, check_ids={"A1", "A4"})

        assert [r.command for r in results] == ["A1 HELP", "A4 THREAD"]


class TestReports:
    def make_results(self) -> list[ValidationResult]:
        return [
            ValidationResult(
                command="A1 HELP",
                sent="HELP",
                passed=True,
                detail="lista de comandos con 4 líneas",
                response_lines=("STAT", "ok"),
                duration_s=0.5,
            ),
            ValidationResult(
                command="A5 DECAID",
                sent="DECAID",
                passed=False,
                detail="Device ID no es de la familia DW3xxx: basura",
                response_lines=("basura sin sentido",),
                duration_s=0.2,
            ),
            ValidationResult(
                command="C4 Sesión TWR (2 placas)",
                sent="RESPF + INITF",
                passed=True,
                detail="SKIP: requiere una segunda placa conectada",
                response_lines=(),
                duration_s=0.0,
            ),
        ]

    def test_summarize_counts_statuses(self) -> None:
        counts = summarize(self.make_results())

        assert counts == {"total": 3, "pass": 1, "fail": 1, "skip": 1}

    def test_write_reports_json_and_markdown(self, tmp_path: Path) -> None:
        results = self.make_results()

        json_path, md_path = write_reports(results, report_dir=tmp_path, port="COM26")

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["puerto"] == "COM26"
        assert payload["resumen"]["fail"] == 1
        assert len(payload["resultados"]) == 3

        markdown = md_path.read_text(encoding="utf-8")
        assert "1 PASS" in markdown and "1 FAIL" in markdown and "1 SKIP" in markdown
        assert "basura sin sentido" in markdown  # respuestas crudas del fallo
        assert md_path.name.startswith("validacion-COM26-")
