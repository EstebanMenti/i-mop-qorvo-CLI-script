"""Tests del muestreo por polling (initiator detrás del puente BLE nRF52840).

El simulador reproduce el comportamiento del banco real BLE: el initiator
acumula notificaciones ``SESSION_INFO_NTF`` (partidas en dos líneas, como el
fw 1.1.0) y las entrega **junto con la respuesta del próximo comando**
(``docs/verificacion-comandos-responder-ble.md`` §3.4) — nunca de forma
espontánea.
"""

import pytest

import dwm3001c_cli.core.client as client_module
from dwm3001c_cli.calibration.autocal import AutocalConfig, autocalibrate
from dwm3001c_cli.calibration.poll_sampler import (
    collect_samples_polled,
    extract_measurements,
)
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


def split_notification(one_line: str) -> list[str]:
    """Parte una notificación en dos líneas, como la emite el fw 1.1.0."""
    marker = "n_measurements=1"
    cut = one_line.index(marker) + len(marker)
    return [one_line[:cut], one_line[cut:]]


def measurement_line(sequence: int, status: str, distance_cm: int | None) -> str:
    inner = f' [mac_address=0x0001, status="{status}"'
    if distance_cm is not None:
        inner += f", distance[cm]={distance_cm}"
    inner += "]}"
    return (
        f"SESSION_INFO_NTF: {{session_handle=1, sequence_number={sequence},"
        f" block_index={sequence}, n_measurements=1{inner}"
    )


class TwrWorld:
    """Estado físico compartido del par simulado (misma física que la guía §4.2)."""

    def __init__(self, real_cm: float, delay: int, ideal_delay: int, fail_all: bool = False):
        self.real_cm = real_cm
        self.delay = delay
        self.ideal_delay = ideal_delay
        self.sensitivity = 0.47
        self.fail_all = fail_all
        self.sequence = 0

    def reported_cm(self) -> int:
        return round(self.real_cm + (self.ideal_delay - self.delay) * self.sensitivity)


class PollInitiator(FakeTransport):
    """Initiator simulado detrás del puente BLE: NO emite notificaciones
    espontáneas; acumula y las entrega recién con la respuesta de ``THREAD``."""

    def __init__(
        self,
        world: TwrWorld,
        per_poll: int = 5,
        silent_polls: bool = False,
        max_total: int | None = None,
    ) -> None:
        super().__init__()
        self.world = world
        self.per_poll = per_poll
        self.silent_polls = silent_polls
        # Simula el estrangulamiento del puente BLE: entrega como máximo
        # ``max_total`` notificaciones en toda la sesión y después calla
        # [caso real 2026-09-08: ventana vencida con 33/100 SUCCESS, tasa 94%].
        self.max_total = max_total
        self._delivered = 0
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
        elif upper == "THREAD":
            if self.silent_polls:
                return  # el puente se traga la respuesta: silencio → timeout
            count = 0
            if self.session_active:
                if self.max_total is None:
                    count = self.per_poll
                else:
                    count = min(self.per_poll, self.max_total - self._delivered)
            for _ in range(count):
                self._delivered += 1
                self.world.sequence += 1
                n = self.world.sequence
                if self.world.fail_all:
                    one_line = measurement_line(n, "RX_TIMEOUT", None)
                else:
                    one_line = measurement_line(n, "SUCCESS", self.world.reported_cm())
                self._pending.extend(split_notification(one_line))
            self._pending.append("ok")


class SimResponder(FakeTransport):
    """Responder simulado: aplica las escrituras de CALKEY al mundo (emulando
    el fw 1.1.0: lectura directa KO, escritura verifica con el valor nuevo)."""

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
            if len(parts) == 3:
                self.world.delay = int(parts[2])
                self._pending.extend([f"{parts[1]}: 0x{self.world.delay:x} (len: 4)", "ok"])
            else:
                self._pending.extend(["", f"Please enter a valid key: {parts[1]}", "KO"])


def make_pair(
    world: TwrWorld,
    *,
    per_poll: int = 5,
    silent_polls: bool = False,
    max_total: int | None = None,
) -> tuple[DwmCliClient, DwmCliClient, PollInitiator, SimResponder]:
    initiator_transport = PollInitiator(
        world, per_poll=per_poll, silent_polls=silent_polls, max_total=max_total
    )
    responder_transport = SimResponder(world)
    initiator = DwmCliClient(initiator_transport, command_timeout_s=0.2, quiet_period_s=0.05)
    responder = DwmCliClient(responder_transport, command_timeout_s=0.2, quiet_period_s=0.05)
    return initiator, responder, initiator_transport, responder_transport


class TestExtractMeasurements:
    def test_reassembles_two_line_notifications(self) -> None:
        one_line = measurement_line(0, "SUCCESS", 210)
        first, second = split_notification(one_line)
        lines = ["THREAD", "hilo 1", first, second, "ok"]

        measurements = extract_measurements(lines, "TEST")

        assert len(measurements) == 1
        assert measurements[0].status == "SUCCESS"
        assert measurements[0].distance_cm == 210

    def test_ignores_non_notification_lines(self) -> None:
        assert extract_measurements(["THREAD", "hilo 1", "ok"], "TEST") == []

    def test_drops_unparseable_notification(self) -> None:
        lines = ["SESSION_INFO_NTF: {n_measurements=1", ' [status="BOGUS"]', "ok"]
        assert extract_measurements(lines, "TEST") == []


class TestCollectSamplesPolled:
    def test_returns_stats_and_stops_boards(self) -> None:
        world = TwrWorld(real_cm=200.0, delay=16439, ideal_delay=16439)
        initiator, responder, initiator_t, responder_t = make_pair(world)

        stats = collect_samples_polled(initiator, responder, n_samples=10)

        assert stats.n_success == 10
        assert stats.mean_cm == pytest.approx(200.0)
        assert "STOP" in initiator_t.sent and "STOP" in responder_t.sent
        assert not initiator_t.session_active

    def test_polls_with_thread_command(self) -> None:
        world = TwrWorld(real_cm=200.0, delay=16439, ideal_delay=16439)
        initiator, responder, initiator_t, responder_t = make_pair(world)

        collect_samples_polled(initiator, responder, n_samples=10)

        polls = [cmd for cmd in initiator_t.sent if cmd == "THREAD"]
        assert len(polls) == 2  # 10 muestras / 5 por poll
        assert any(cmd.startswith("INITF") for cmd in initiator_t.sent)
        assert any(cmd.startswith("RESPF") for cmd in responder_t.sent)

    def test_on_measurement_called_for_every_measurement(self) -> None:
        world = TwrWorld(real_cm=200.0, delay=16439, ideal_delay=16439)
        initiator, responder, _, _ = make_pair(world)
        seen: list[str] = []

        collect_samples_polled(
            initiator, responder, n_samples=10, on_measurement=lambda m: seen.append(m.status)
        )

        assert seen.count("SUCCESS") == 10

    def test_bad_link_raises_before_calibrating(self) -> None:
        world = TwrWorld(real_cm=200.0, delay=16375, ideal_delay=16439, fail_all=True)
        initiator, responder, _, responder_t = make_pair(world)

        with pytest.raises(CalibrationError, match="SUCCESS"):
            collect_samples_polled(initiator, responder, n_samples=10, timeout_s=5.0)

        assert not any(cmd.startswith("CALKEY") for cmd in responder_t.sent)

    def test_consecutive_poll_timeouts_abort(self) -> None:
        world = TwrWorld(real_cm=200.0, delay=16439, ideal_delay=16439)
        initiator_transport = PollInitiator(world, silent_polls=True)
        responder_transport = SimResponder(world)
        initiator = DwmCliClient(initiator_transport, command_timeout_s=0.05, quiet_period_s=0.02)
        responder = DwmCliClient(responder_transport, command_timeout_s=0.2, quiet_period_s=0.05)

        with pytest.raises(CalibrationError, match="polling"):
            collect_samples_polled(
                initiator,
                responder,
                n_samples=10,
                max_consecutive_timeouts=3,
                poll_interval_s=0.0,
            )

    def test_partial_healthy_sample_is_accepted(self) -> None:
        # Caso real 2026-09-08: el puente BLE estranguló la entrega y la
        # ventana venció con 33/100 SUCCESS (tasa 94%): la muestra parcial
        # sana debe aceptarse en vez de abortar la calibración.
        world = TwrWorld(real_cm=200.0, delay=16439, ideal_delay=16439)
        initiator, responder, _, _ = make_pair(world, max_total=33)

        stats = collect_samples_polled(
            initiator, responder, n_samples=100, timeout_s=2.0, poll_interval_s=0.0
        )

        assert stats.n_success == 33
        assert stats.mean_cm == pytest.approx(200.0)

    def test_partial_poor_sample_still_rejected(self) -> None:
        world = TwrWorld(real_cm=200.0, delay=16439, ideal_delay=16439)
        initiator, responder, _, _ = make_pair(world, max_total=10)

        with pytest.raises(CalibrationError, match="Enlace pobre"):
            collect_samples_polled(
                initiator, responder, n_samples=100, timeout_s=1.0, poll_interval_s=0.0
            )


CONFIG = AutocalConfig(n_samples=10, tolerance_cm=2.0, probe_step_units=20)


class TestAutocalibrateWithPollSampler:
    def test_converges_using_poll_sampler(self, tmp_path) -> None:
        world = TwrWorld(real_cm=200.0, delay=16375, ideal_delay=16439)
        initiator, responder, initiator_t, _ = make_pair(world)
        seen: list[str] = []

        report = autocalibrate(
            responder,
            initiator,
            real_distance_m=2.0,
            config=CONFIG,
            report_dir=tmp_path,
            sampler=collect_samples_polled,
            on_measurement=lambda m: seen.append(m.status),
        )

        assert report.converged
        assert abs(report.final_delay - world.ideal_delay) <= 5
        assert any(cmd == "THREAD" for cmd in initiator_t.sent)
        assert seen.count("SUCCESS") >= 30  # 3+ mediciones de 10 muestras

    def test_sampler_receives_on_measurement_and_params(self, tmp_path) -> None:
        world = TwrWorld(real_cm=200.0, delay=16439, ideal_delay=16439)
        initiator, responder, _, _ = make_pair(world)
        calls: list[dict] = []

        def recording_sampler(*args, **kwargs):
            calls.append(dict(kwargs))
            return collect_samples_polled(*args, **kwargs)

        autocalibrate(
            responder,
            initiator,
            real_distance_m=2.0,
            config=CONFIG,
            report_dir=tmp_path,
            sampler=recording_sampler,
            on_measurement=lambda m: None,
        )

        assert calls, "el sampler inyectable no fue invocado"
        assert calls[0]["n_samples"] == 10
        assert "on_measurement" in calls[0]
        assert calls[0]["session_params"].chan == 9
