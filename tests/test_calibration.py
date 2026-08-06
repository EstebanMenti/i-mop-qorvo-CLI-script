"""Tests de la calibración automática (fase F4) contra un firmware simulado.

El simulador reproduce la física del problema: la distancia reportada depende
del retardo de antena escrito en el responder, con la regla de signo real
(aumentar el delay reduce la distancia). También emula los comportamientos del
fw 1.1.0: lectura de CALKEY rota (KO) y escritura que responde el valor nuevo.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

import dwm3001c_cli.core.client as client_module
from dwm3001c_cli.calibration.autocal import AutocalConfig, autocalibrate
from dwm3001c_cli.calibration.sampler import SessionParams, collect_samples
from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.core.errors import CalibrationError
from fakes import FakeTransport


@pytest.fixture(autouse=True)
def fast_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anula la espera post-STOP para que los tests no duerman."""
    monkeypatch.setattr(client_module, "_STOP_SETTLE_S", 0.0)


def js_stat_none() -> list[str]:
    return [
        'JS0080{"Info":{"Device":"SIM","Current App":"NONE","Version":"1.1.0",'
        '"Build":"B","Apps":["LISTENER","RESPF","INITF"],"Driver":"D","UWB stack":"S"}}',
        "ok",
    ]


@dataclass
class TwrWorld:
    """Estado físico compartido del par simulado.

    ``reported = real + (ideal_delay - delay) * sensitivity`` — con el delay
    calibrado en ``ideal_delay`` la placa reporta la distancia real; con un
    delay menor, reporta de más (regla de signo de la guía §4.2).
    """

    real_cm: float
    delay: int
    ideal_delay: int
    sensitivity: float = 0.47
    fail_all: bool = False
    sequence: int = 0

    def reported_cm(self) -> int:
        return round(self.real_cm + (self.ideal_delay - self.delay) * self.sensitivity)


class SimInitiator(FakeTransport):
    """Placa initiator simulada: emite notificaciones mientras la sesión corre."""

    def __init__(self, world: TwrWorld) -> None:
        super().__init__()
        self.world = world
        self.session_active = False

    def write_line(self, line: str) -> None:
        self.sent.append(line)
        upper = line.upper()
        if upper.startswith("INITF"):
            self.session_active = True
            self._pending.append("ok")
        elif upper == "STOP":
            self.session_active = False
            self._pending.append("ok")
        elif upper == "STAT":
            self._pending.extend(js_stat_none())

    def read_line(self, timeout_s: float) -> str | None:
        if self._pending:
            return self._pending.popleft()
        if not self.session_active:
            return None
        self.world.sequence += 1
        n = self.world.sequence
        if self.world.fail_all:
            return (
                f"SESSION_INFO_NTF: {{session_handle=1, sequence_number={n},"
                f" block_index={n}, n_measurements=1 [mac_address=0x0001,"
                ' status="RX_TIMEOUT"]}'
            )
        return (
            f"SESSION_INFO_NTF: {{session_handle=1, sequence_number={n},"
            f" block_index={n}, n_measurements=1 [mac_address=0x0001,"
            f' status="SUCCESS", distance[cm]={self.world.reported_cm()}]}}'
        )


class SimResponder(FakeTransport):
    """Placa responder simulada: aplica las escrituras de CALKEY al mundo.

    Emula el fw 1.1.0: la lectura directa de CALKEY responde KO; la escritura
    funciona y responde el valor nuevo; LISTCAL refleja el delay vigente.
    """

    def __init__(self, world: TwrWorld) -> None:
        super().__init__()
        self.world = world

    def write_line(self, line: str) -> None:
        self.sent.append(line)
        upper = line.upper()
        if upper.startswith("RESPF") or upper == "STOP" or upper == "SAVE":
            self._pending.append("ok")
        elif upper == "STAT":
            self._pending.extend(js_stat_none())
        elif upper == "LISTCAL":
            self._pending.extend([f"ant0.ch9.ant_delay: 0x{self.world.delay:x} (len: 4)", "ok"])
        elif upper.startswith("CALKEY "):
            parts = line.split()
            if len(parts) == 3:  # escritura: aplica y responde el valor nuevo
                self.world.delay = int(parts[2])
                self._pending.extend([f"{parts[1]}: 0x{self.world.delay:x} (len: 4)", "ok"])
            else:  # lectura directa: rota en fw 1.1.0
                self._pending.extend(["", f"Please enter a valid key: {parts[1]}", "KO"])


def make_pair(world: TwrWorld) -> tuple[DwmCliClient, DwmCliClient, SimInitiator, SimResponder]:
    initiator_transport = SimInitiator(world)
    responder_transport = SimResponder(world)
    initiator = DwmCliClient(initiator_transport, command_timeout_s=0.2)
    responder = DwmCliClient(responder_transport, command_timeout_s=0.2)
    return initiator, responder, initiator_transport, responder_transport


CONFIG = AutocalConfig(n_samples=20, tolerance_cm=2.0, probe_step_units=20)


class TestSimulator:
    def test_sign_convention_more_delay_less_distance(self) -> None:
        world = TwrWorld(real_cm=200.0, delay=16375, ideal_delay=16439)

        before = world.reported_cm()
        world.delay += 100
        after = world.reported_cm()

        assert after < before  # guía §4.2: subir el retardo baja la distancia


class TestCollectSamples:
    def test_returns_stats_and_stops_boards(self) -> None:
        world = TwrWorld(real_cm=200.0, delay=16439, ideal_delay=16439)
        initiator, responder, initiator_t, responder_t = make_pair(world)

        stats = collect_samples(initiator, responder, n_samples=10)

        assert stats.n_success == 10
        assert stats.mean_cm == pytest.approx(200.0)
        assert stats.std_cm == 0.0
        assert "STOP" in initiator_t.sent and "STOP" in responder_t.sent
        assert not initiator_t.session_active  # sesión detenida al salir

    def test_sends_full_parameter_set(self) -> None:
        world = TwrWorld(real_cm=200.0, delay=16439, ideal_delay=16439)
        initiator, responder, initiator_t, responder_t = make_pair(world)

        collect_samples(initiator, responder, n_samples=3, session_params=SessionParams(chan=9))

        initf = next(cmd for cmd in initiator_t.sent if cmd.startswith("INITF"))
        respf = next(cmd for cmd in responder_t.sent if cmd.startswith("RESPF"))
        for fragment in ("-CHAN=9", "-BLOCK=200", "-RRU=DSTWR", "-ID=42", "-ADDR=0", "-PADDR=1"):
            assert fragment in initf
        assert "-ADDR=1" in respf and "-PADDR=0" in respf

    def test_bad_link_raises_before_calibrating(self) -> None:
        world = TwrWorld(real_cm=200.0, delay=16375, ideal_delay=16439, fail_all=True)
        initiator, responder, _, responder_t = make_pair(world)

        with pytest.raises(CalibrationError, match="SUCCESS"):
            collect_samples(initiator, responder, n_samples=10, timeout_s=0.3)

        assert not any(cmd.startswith("CALKEY") for cmd in responder_t.sent)


class TestAutocalibrate:
    def test_converges_from_30cm_error(self, tmp_path: Path) -> None:
        # delay de fábrica 64 unidades por debajo del ideal → error ≈ +30 cm.
        world = TwrWorld(real_cm=200.0, delay=16375, ideal_delay=16439)
        initiator, responder, _, _ = make_pair(world)

        report = autocalibrate(
            responder,
            initiator,
            real_distance_m=2.0,
            config=CONFIG,
            report_dir=tmp_path,
        )

        assert report.converged
        assert abs(report.final_delay - world.ideal_delay) <= 5
        assert abs(world.delay - world.ideal_delay) <= 5  # aplicado en la "placa"
        assert len(report.iterations) <= 4  # inicial + sondeo + ≤2 correcciones
        assert report.sensitivity_cm_per_unit == pytest.approx(0.47, abs=0.1)
        assert report.saved
        assert abs(report.iterations[-1].error_cm) <= CONFIG.tolerance_cm

    def test_already_calibrated_makes_no_writes(self, tmp_path: Path) -> None:
        world = TwrWorld(real_cm=200.0, delay=16439, ideal_delay=16439)
        initiator, responder, _, responder_t = make_pair(world)

        report = autocalibrate(
            responder, initiator, real_distance_m=2.0, config=CONFIG, report_dir=tmp_path
        )

        assert report.converged
        assert report.final_delay == 16439
        assert len(report.iterations) == 1  # solo la medición inicial
        writes = [c for c in responder_t.sent if c.startswith("CALKEY") and len(c.split()) == 3]
        assert writes == []

    def test_no_sensitivity_restores_original_delay(self, tmp_path: Path) -> None:
        # Sensibilidad nula: el delay no afecta la distancia → sondeo sin efecto.
        world = TwrWorld(real_cm=200.0, delay=16375, ideal_delay=16439, sensitivity=0.0)
        world.real_cm = 230.0  # el sim reporta 230 constante; real declarada 200 → error +30
        initiator, responder, _, _ = make_pair(world)

        with pytest.raises(CalibrationError, match="escalón"):
            autocalibrate(
                responder, initiator, real_distance_m=2.0, config=CONFIG, report_dir=tmp_path
            )

        assert world.delay == 16375  # restaurado

    def test_safeguard_max_correction_restores(self, tmp_path: Path) -> None:
        # Ideal a +4000 unidades (≈19 m de error): la corrección excede ±1500.
        world = TwrWorld(real_cm=200.0, delay=16375, ideal_delay=20375)
        initiator, responder, _, _ = make_pair(world)

        with pytest.raises(CalibrationError, match="Salvaguarda"):
            autocalibrate(
                responder, initiator, real_distance_m=2.0, config=CONFIG, report_dir=tmp_path
            )

        assert world.delay == 16375  # restaurado

    def test_writes_backup_and_reports(self, tmp_path: Path) -> None:
        world = TwrWorld(real_cm=200.0, delay=16375, ideal_delay=16439)
        initiator, responder, _, _ = make_pair(world)

        autocalibrate(responder, initiator, real_distance_m=2.0, config=CONFIG, report_dir=tmp_path)

        backups = list(tmp_path.glob("backup-calkey-FAKE-*.json"))
        assert len(backups) == 1
        assert json.loads(backups[0].read_text(encoding="utf-8"))["valor"] == 16375

        report_jsons = list(tmp_path.glob("calibracion-FAKE-*.json"))
        report_mds = list(tmp_path.glob("calibracion-FAKE-*.md"))
        assert len(report_jsons) == 1 and len(report_mds) == 1
        payload = json.loads(report_jsons[0].read_text(encoding="utf-8"))
        assert payload["converged"] is True
        assert payload["initial_delay"] == 16375
        markdown = report_mds[0].read_text(encoding="utf-8")
        assert "CONVERGIÓ" in markdown and "ant0.ch9.ant_delay" in markdown

    def test_invalid_distance_rejected_before_touching_board(self) -> None:
        world = TwrWorld(real_cm=200.0, delay=16375, ideal_delay=16439)
        initiator, responder, _, responder_t = make_pair(world)

        with pytest.raises(ValueError, match="Distancia"):
            autocalibrate(responder, initiator, real_distance_m=0.0, config=CONFIG)

        assert responder_t.sent == []
