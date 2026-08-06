"""Tests de DwmCliClient contra el transporte simulado (fase F2)."""

from pathlib import Path

import pytest

from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.core.errors import (
    CommandRejectedError,
    CommandTimeoutError,
    UnexpectedModeError,
)
from fakes import FakeTransport

FIXTURES = Path(__file__).parent / "fixtures"

STAT_REAL = (FIXTURES / "stat_fw110_real.txt").read_text(encoding="utf-8").splitlines()

STAT_RUNNING_INITF = [
    "STAT",
    'JS0080{"Info":{"Device":"X","Current App":"INITF","Version":"1.1.0",'
    '"Build":"B","Apps":["LISTENER","RESPF","INITF"],"Driver":"D","UWB stack":"S"}}',
    "ok",
]


def ntf_line(n: int) -> str:
    return (
        f"SESSION_INFO_NTF: {{session_handle=1, sequence_number={n}, block_index={n},"
        ' n_measurements=1 [mac_address=0x0001, status="SUCCESS", distance[cm]=200]}'
    )


def make_client(
    script: dict[str, list[str]] | None = None, notifications: list[str] | None = None
) -> tuple[DwmCliClient, FakeTransport]:
    transport = FakeTransport(script=script, notifications=notifications or [])
    return DwmCliClient(transport, command_timeout_s=0.2), transport


class TestSendCommand:
    def test_discards_echo_and_stops_at_ok(self) -> None:
        client, _ = make_client({"STAT": STAT_REAL})

        lines = client.send_command("STAT")

        assert lines[0].startswith("JS0109")  # el eco "STAT" fue descartado
        assert lines[-1] == "ok"

    def test_silence_raises_timeout_with_context(self) -> None:
        client, _ = make_client({})

        with pytest.raises(CommandTimeoutError) as exc_info:
            client.send_command("STAT", timeout_s=0.05)

        assert exc_info.value.port == "FAKE"
        assert exc_info.value.command == "STAT"

    def test_stops_at_ko_error_marker(self) -> None:
        # Respuesta real de fw 1.1.0 ante una clave inexistente (2026-08-06).
        client, _ = make_client(
            {"CALKEY nada": ["", "Please enter a valid key: nada", "", "", "KO"]}
        )

        lines = client.send_command("CALKEY nada")

        assert lines[-1] == "KO"

    def test_quiet_period_ends_collection_without_ok(self) -> None:
        client, _ = make_client({"THREAD": ["linea 1", "linea 2"]})

        assert client.send_command("THREAD") == ["linea 1", "linea 2"]


class TestStatAndMode:
    def test_stat_parses_real_output(self) -> None:
        client, _ = make_client({"STAT": STAT_REAL})

        info = client.stat()

        assert info.mode == "NONE"
        assert info.version == "1.1.0"

    def test_ensure_mode_none_when_already_none(self) -> None:
        client, transport = make_client({"STOP": ["ok"], "STAT": STAT_REAL})

        client.ensure_mode_none()

        assert transport.sent == ["STOP", "STAT"]

    def test_ensure_mode_none_raises_if_app_persists(self) -> None:
        client, transport = make_client({"STOP": ["ok"], "STAT": STAT_RUNNING_INITF})

        with pytest.raises(UnexpectedModeError, match="INITF"):
            client.ensure_mode_none()

        # Debe haber reintentado una vez: STOP,STAT,STOP,STAT.
        assert transport.sent == ["STOP", "STAT", "STOP", "STAT"]

    def test_stop_tolerates_silence(self) -> None:
        client, _ = make_client({})

        client.stop()  # no debe lanzar


class TestCalKeys:
    def test_calkey_read(self) -> None:
        client, _ = make_client(
            {"CALKEY ant0.ch9.ant_delay": ["ant0.ch9.ant_delay: 0x4015 (len: 4)", "ok"]}
        )

        cal_key = client.calkey_read("ant0.ch9.ant_delay")

        assert cal_key.value == 0x4015

    def test_calkey_read_without_key_in_response_raises(self) -> None:
        client, _ = make_client({"CALKEY nada": ["error: unknown key", "ok"]})

        with pytest.raises(CommandRejectedError, match="nada"):
            client.calkey_read("nada")

    def test_calkey_write_verifies_by_rereading(self) -> None:
        client, transport = make_client(
            {
                "CALKEY xtal_trim 50": ["xtal_trim: 0x32 (len: 1)", "ok"],
                "CALKEY xtal_trim": ["xtal_trim: 0x32 (len: 1)", "ok"],
            }
        )

        written = client.calkey_write("xtal_trim", 50)  # 50 == 0x32

        assert written.value == 50
        assert transport.sent == ["CALKEY xtal_trim 50", "CALKEY xtal_trim"]

    def test_calkey_write_mismatch_raises(self) -> None:
        client, _ = make_client(
            {
                "CALKEY xtal_trim 7": ["xtal_trim: 0x07 (len: 1)", "ok"],
                "CALKEY xtal_trim": ["xtal_trim: 0x32 (len: 1)", "ok"],  # relee otro valor
            }
        )

        with pytest.raises(CommandRejectedError, match="sospechoso"):
            client.calkey_write("xtal_trim", 7)

    def test_calkey_write_negative_value_raises(self) -> None:
        client, _ = make_client({})

        with pytest.raises(ValueError, match="negativo"):
            client.calkey_write("xtal_trim", -1)

    def test_listcal(self) -> None:
        client, _ = make_client({"LISTCAL": ["LISTCAL", "xtal_trim: 0x32 (len: 1)", "", "ok"]})

        keys = client.listcal()

        assert keys["xtal_trim"].value == 0x32


class TestServiceCommands:
    def test_save_requires_ok(self) -> None:
        client, _ = make_client({"SAVE": ["error: not allowed"]})

        with pytest.raises(CommandRejectedError, match="SAVE"):
            client.save()

    def test_save_ok(self) -> None:
        client, _ = make_client({"SAVE": ["ok"]})

        client.save()

    def test_diag_sends_numeric_flag(self) -> None:
        client, transport = make_client({"DIAG 1": ["ok"], "DIAG 0": ["ok"]})

        client.diag(True)
        client.diag(False)

        assert transport.sent == ["DIAG 1", "DIAG 0"]

    def test_setapp_validates_app_name(self) -> None:
        client, _ = make_client({})

        with pytest.raises(ValueError, match="inválida"):
            client.setapp("OTRA")

    def test_setapp_normalizes_to_uppercase(self) -> None:
        client, transport = make_client({"SETAPP NONE": ["ok"]})

        client.setapp("none")

        assert transport.sent == ["SETAPP NONE"]

    def test_decaid_parses(self) -> None:
        client, _ = make_client(
            {
                "DECAID": [
                    "Qorvo Device ID = 0xdeca0304",
                    "Qorvo Lot ID    = 0x0000503639463438",
                    "Qorvo Part ID   = 0x8124d5b7",
                    "Qorvo SoC ID    = 00005036394634388124d5b7",
                    "ok",
                ]
            }
        )

        assert client.decaid().device_id == "0xdeca0304"


class TestAppCommands:
    def test_initf_builds_full_command_line(self) -> None:
        command = (
            "INITF -CHAN=9 -PRFSET=BPRF4 -SLOT=2400 -BLOCK=200 -ROUND=25 "
            "-RRU=DSTWR -ID=42 -VUPPER=01:02:03:04:05:06:07:08 -ADDR=0 -PADDR=1"
        )
        client, transport = make_client({command: ["ok"]})

        client.start_initf(
            chan=9,
            prfset="BPRF4",
            slot=2400,
            block=200,
            round=25,
            rru="DSTWR",
            id=42,
            vupper="01:02:03:04:05:06:07:08",
            addr=0,
            paddr=1,
        )

        assert transport.sent == [command]

    def test_respf_without_params(self) -> None:
        client, transport = make_client({"RESPF": ["ok"]})

        client.start_respf()

        assert transport.sent == ["RESPF"]

    def test_invalid_channel_raises_before_sending(self) -> None:
        client, transport = make_client({})

        with pytest.raises(ValueError, match="canal 5 o 9"):
            client.start_initf(chan=7)

        assert transport.sent == []

    def test_unknown_option_raises_before_sending(self) -> None:
        client, transport = make_client({})

        with pytest.raises(ValueError, match="desconocidas"):
            client.start_initf(power=5)

        assert transport.sent == []

    def test_out_of_range_slot_raises(self) -> None:
        client, _ = make_client({})

        with pytest.raises(ValueError, match="slot"):
            client.start_initf(slot=100)


class TestNotifications:
    def test_reads_up_to_max_count(self) -> None:
        notifications = [ntf_line(i) for i in range(5)]
        client, _ = make_client({"INITF": ["ok"]}, notifications=notifications)

        measurements = client.read_notifications(max_count=3)

        assert [m.sequence_number for m in measurements] == [0, 1, 2]
        assert all(m.distance_cm == 200 for m in measurements)

    def test_ignores_non_notification_lines(self) -> None:
        client, _ = make_client({}, notifications=["basura", ntf_line(1), "otra basura"])

        measurements = client.read_notifications(max_count=10)

        assert len(measurements) == 1

    def test_requires_a_stop_condition(self) -> None:
        client, _ = make_client({})

        with pytest.raises(ValueError, match="duration_s"):
            client.read_notifications()

    def test_callback_receives_each_measurement(self) -> None:
        received: list[int] = []
        client, _ = make_client({}, notifications=[ntf_line(4)])

        client.read_notifications(
            max_count=1, on_measurement=lambda m: received.append(m.sequence_number)
        )

        assert received == [4]
