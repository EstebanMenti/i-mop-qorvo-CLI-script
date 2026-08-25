"""Tests de los parsers del firmware (fase F2).

Fixtures: ``stat_fw110_real.txt`` es una captura real de una DWM3001CDK con
firmware 1.1.0 (2026-08-06); las demás provienen de los ejemplos textuales de
la guía (docs/referencias/guia-cli-calibracion-dwm3001cdk.md).
"""

from pathlib import Path

import pytest

from dwm3001c_cli.core.parsers import (
    is_ok,
    parse_calkey_line,
    parse_decaid,
    parse_listcal,
    parse_session_info,
    parse_stat,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Ejemplo completo de la guía §2.4 (con DIAG habilitado → incluye RSSI).
SESSION_INFO_FULL = (
    "SESSION_INFO_NTF: {session_handle=1, sequence_number=0, block_index=0, n_measurements=1"
    ' [mac_address=0x0001, status="SUCCESS", distance[cm]=91, loc_az_pdoa=65.35, loc_az=24.90,'
    " loc_el_pdoa=32.12, loc_el=12.01, rmt_az=22.84, rmt_el=13.59, RSSI[dBm]=-66.5]}"
)


def load_fixture_lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


class TestParseStat:
    def test_real_fw110_output_without_mode_line(self) -> None:
        # El eco ("STAT") lo descarta el cliente; el parser igual lo tolera.
        lines = load_fixture_lines("stat_fw110_real.txt")

        info = parse_stat(lines)

        assert info.mode == "NONE"  # derivado de "Current App"
        assert info.device == "DWM3001CDK - DW3_QM33_SDK - FreeRTOS"
        assert info.current_app == "NONE"
        assert info.version == "1.1.0"
        assert info.build == "Aug 13 2025 14:23:02"
        assert info.apps == ("LISTENER", "RESPF", "INITF")
        assert info.driver == "DW3XXX Device Driver Version 08.19.02"
        assert info.uwb_stack == "R12.7.0-405-gb33c5c4272"

    def test_manual_style_output_with_mode_line(self) -> None:
        # Variante del manual (guía §2.1): línea MODE + JSON en una sola línea.
        lines = [
            "MODE: NONE",
            'JS0108{"Info":{"Device":"X","Current App":"NONE","Version":"1.0.0",'
            '"Build":"B","Apps":["LISTENER","RESPF","INITF"],"Driver":"D","UWB stack":"S"}}',
        ]

        info = parse_stat(lines)

        assert info.mode == "NONE"
        assert info.version == "1.0.0"

    def test_mode_line_wins_over_current_app(self) -> None:
        lines = [
            "MODE: INITF",
            'JS0100{"Info":{"Current App":"INITF","Device":"X"}}',
        ]

        assert parse_stat(lines).mode == "INITF"

    def test_pegged_echo_after_async_app_output(self) -> None:
        # [Verificado 2026-08-25, hardware real, USB directo] Al enviar STAT
        # con LISTENER ya corriendo, la salida asíncrona de la app gana la
        # detección de eco de send_command (que solo mira la primera línea);
        # el eco real de STAT llega después, pegado sin separador al bloque
        # JSON: "STAT\rJS010D{...}".
        lines = [
            "Found non-AOA chip. PDoA is not available.",
            "Listener Top Application: Started",
            'STAT\rJS010D{"Info":{',
            '"Device":"DWM3001CDK - DW3_QM33_SDK - FreeRTOS",',
            '"Current App":"LISTENER",',
            '"Version":"1.1.0",',
            '"Build":"Aug 10 2026 16:03:38",',
            '"Apps":["LISTENER","RESPF","INITF"],',
            '"Driver":"DW3XXX Device Driver Version 08.19.02",',
            '"UWB stack":"R12.7.0-405-gb33c5c4272"}}',
            "",
            "ok",
        ]

        info = parse_stat(lines)

        assert info.current_app == "LISTENER"
        assert info.mode == "LISTENER"

    def test_pegged_echo_after_session_notifications(self) -> None:
        # [Verificado 2026-08-25, hardware real, USB directo] Mismo caso que
        # el anterior pero tras una sesión INITF/RESPF activa: antes del eco
        # pegado llegan varias notificaciones SESSION_STATUS_NTF/SESSION_INFO_NTF.
        lines = [
            'SESSION_STATUS_NTF: {state="INIT", reason="State change"}',
            'SESSION_STATUS_NTF: {state="IDLE", reason="State change"}',
            'SESSION_STATUS_NTF: {state="ACTIVE", reason="State change"}',
            "SESSION_INFO_NTF: {session_handle=1, sequence_number=0, block_index=0,"
            " n_measurements=1",
            '\r [mac_address=0x0001, status="RX_TIMEOUT"]}',
            'STAT\rJS010A{"Info":{',
            '"Device":"DWM3001CDK - DW3_QM33_SDK - FreeRTOS",',
            '"Current App":"INITF",',
            '"Version":"1.1.0",',
            '"Build":"Aug 10 2026 16:03:38",',
            '"Apps":["LISTENER","RESPF","INITF"],',
            '"Driver":"DW3XXX Device Driver Version 08.19.02",',
            '"UWB stack":"R12.7.0-405-gb33c5c4272"}}',
            "",
            "ok",
        ]

        info = parse_stat(lines)

        assert info.current_app == "INITF"
        assert info.mode == "INITF"

    def test_output_without_js_block_raises(self) -> None:
        with pytest.raises(ValueError, match="JSxxxx"):
            parse_stat(["MODE: NONE", "basura"])

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError, match="inválido"):
            parse_stat(['JS0010{"Info":{roto'])


class TestParseCalkey:
    def test_example_from_manual(self) -> None:
        cal_key = parse_calkey_line("xtal_trim: 0x32 (len: 1)")

        assert cal_key.name == "xtal_trim"
        assert cal_key.value == 0x32
        assert cal_key.length_bytes == 1

    def test_namespaced_key(self) -> None:
        cal_key = parse_calkey_line("ant0.ch9.ant_delay: 0x4015 (len: 4)")

        assert cal_key.name == "ant0.ch9.ant_delay"
        assert cal_key.value == 16405

    def test_unrecognized_line_raises(self) -> None:
        with pytest.raises(ValueError, match="no reconocida"):
            parse_calkey_line("cualquier cosa")


class TestParseListcal:
    def test_manual_excerpt(self) -> None:
        keys = parse_listcal(load_fixture_lines("listcal_manual.txt"))

        assert len(keys) == 13
        assert keys["ant0.ch9.ant_delay"].value == 0x4015
        assert keys["restricted_channels"].length_bytes == 2
        assert keys["experimental.mac.session_scheduler.id"].value == 0

    def test_ignores_echo_empty_and_ok(self) -> None:
        keys = parse_listcal(["LISTCAL", "", "xtal_trim: 0x32 (len: 1)", "ok"])

        assert list(keys) == ["xtal_trim"]

    def test_garbage_line_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_listcal(["esto no es una clave"])


class TestParseSessionInfo:
    def test_full_example_from_manual(self) -> None:
        measurement = parse_session_info(SESSION_INFO_FULL)

        assert measurement.sequence_number == 0
        assert measurement.block_index == 0
        assert measurement.mac_address == "0x0001"
        assert measurement.status == "SUCCESS"
        assert measurement.distance_cm == 91
        assert measurement.rssi_dbm == -66.5

    def test_without_rssi(self) -> None:
        line = (
            "SESSION_INFO_NTF: {session_handle=1, sequence_number=7, block_index=7,"
            ' n_measurements=1 [mac_address=0x0001, status="SUCCESS", distance[cm]=203]}'
        )

        measurement = parse_session_info(line)

        assert measurement.distance_cm == 203
        assert measurement.rssi_dbm is None

    def test_failed_round_without_distance(self) -> None:
        line = (
            "SESSION_INFO_NTF: {session_handle=1, sequence_number=9, block_index=9,"
            ' n_measurements=1 [mac_address=0x0001, status="RX_TIMEOUT"]}'
        )

        measurement = parse_session_info(line)

        assert measurement.status == "RX_TIMEOUT"
        assert measurement.distance_cm is None

    def test_non_notification_line_raises(self) -> None:
        with pytest.raises(ValueError, match="SESSION_INFO_NTF"):
            parse_session_info("ok")

    def test_missing_required_field_raises(self) -> None:
        line = "SESSION_INFO_NTF: {sequence_number=1, block_index=1, mac_address=0x01}"
        with pytest.raises(ValueError, match="status"):
            parse_session_info(line)


class TestParseDecaid:
    def test_example_from_manual(self) -> None:
        lines = [
            "Qorvo Device ID = 0xdeca0304",
            "Qorvo Lot ID    = 0x0000503639463438",
            "Qorvo Part ID   = 0x8124d5b7",
            "Qorvo SoC ID    = 00005036394634388124d5b7",
        ]

        chip = parse_decaid(lines)

        assert chip.device_id == "0xdeca0304"
        assert chip.lot_id == "0x0000503639463438"
        assert chip.part_id == "0x8124d5b7"
        assert chip.soc_id == "00005036394634388124d5b7"

    def test_missing_field_raises(self) -> None:
        with pytest.raises(ValueError, match="soc_id"):
            parse_decaid(["Qorvo Device ID = 0x1", "Qorvo Lot ID = 0x2", "Qorvo Part ID = 0x3"])


class TestIsOk:
    def test_detects_ok_case_insensitive(self) -> None:
        assert is_ok(["algo", " OK "])
        assert is_ok(["ok"])

    def test_no_ok(self) -> None:
        assert not is_ok(["error: algo", "okey"])
