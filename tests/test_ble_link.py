"""Tests de BleTransport (rama hardware/ble-bridge-nrf52840), sin hardware."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

from dwm3001c_cli.core.errors import TransportError
from dwm3001c_cli.transport import ble_link as ble_link_module
from dwm3001c_cli.transport.ble_link import BleTransport
from fakes import FakeBleakClient

ADDRESS = "FD:7A:90:57:CC:9F"


def make_transport(
    fake_client: FakeBleakClient | None = None,
) -> tuple[BleTransport, FakeBleakClient]:
    client = fake_client or FakeBleakClient(ADDRESS)

    def factory(
        address: str, disconnected_callback: Callable[[object], None] | None = None
    ) -> FakeBleakClient:
        client._disconnected_callback = disconnected_callback
        return client

    transport = BleTransport(
        ADDRESS, power_on_settle_s=0.0, power_drain_s=0.05, _client_factory=factory
    )
    return transport, client


class TestLifecycle:
    def test_open_connects_and_powers_on_module(self) -> None:
        transport, client = make_transport()

        with transport:
            assert client.is_connected

        assert b"qorvo on\n" in client.sent
        assert not client.is_connected

    def test_connect_failure_raises_transport_error(self) -> None:
        fake = FakeBleakClient(ADDRESS, fail_connect=True)
        transport, _ = make_transport(fake)

        with pytest.raises(TransportError):
            transport.open()

        transport.close()  # no debe fallar aunque nunca haya llegado a conectar

    def test_name_is_filename_safe(self) -> None:
        transport, _ = make_transport()

        assert transport.name == "BLE-FD7A9057CC9F"
        assert ":" not in transport.name


class TestWriteLine:
    def test_prefixes_with_qorvo_and_newline(self) -> None:
        transport, client = make_transport()

        with transport:
            transport.write_line("STAT")

        assert b"qorvo STAT\n" in client.sent

    def test_reconnects_automatically_after_disconnect(self) -> None:
        fake = FakeBleakClient(ADDRESS)
        transport, client = make_transport(fake)

        with transport:
            client.simulate_disconnect()
            assert not client.is_connected

            transport.write_line("STAT")

            assert client.is_connected
            assert b"qorvo STAT\n" in client.sent

    def test_resets_leftover_partial_line_before_new_command(self) -> None:
        # [Bug real, 2026-08-13] Una notificación BLE perdida (Notify no
        # tiene ACK/retry) puede dejar un fragmento sin "\n" de cierre
        # colgado en el buffer para siempre. Confirmado con hardware real:
        # ese fragmento reaparecía pegado a la respuesta de un comando
        # totalmente distinto, minutos después. write_line() debe descartar
        # cualquier resto antes de mandar el siguiente comando.
        fake = FakeBleakClient(ADDRESS)
        fake.script["STAT"] = [b"stat\r\nJS0109{}\r\n\r\nok\r\n"]
        transport, client = make_transport(fake)

        with transport:
            # Simula el eco truncado de un comando anterior que perdio su
            # notificacion de cierre: llega sin "\n", queda a medio terminar.
            assert client._notify_callback is not None
            client._notify_callback(None, bytearray(b"CALKEY leftover_sin_cierre"))

            transport.write_line("STAT")
            lines: list[str] = []
            while (line := transport.read_line(0.2)) is not None:
                lines.append(line)

        assert "leftover_sin_cierre" not in " ".join(lines)
        assert lines == ["stat", "JS0109{}", "", "ok"]


class TestReadLine:
    def test_reassembles_fragments_and_filters_shell_prompt(self) -> None:
        # Fragmentación real observada contra hardware (2026-08-13): las
        # notificaciones BLE no están alineadas a líneas, y el shell de
        # Zephyr agrega un prompt literal al final de cada respuesta.
        fake = FakeBleakClient(ADDRESS)
        fake.script["STAT"] = [
            b"\r\n",
            b"stat\r\nJS0109",
            b'{"a":1}\r\n\r\nok\r\n\r\nbt_nus:~$ \r\n',
        ]
        transport, _ = make_transport(fake)

        with transport:
            transport.write_line("STAT")
            lines: list[str] = []
            while (line := transport.read_line(0.2)) is not None:
                lines.append(line)

        assert lines == ["", "stat", 'JS0109{"a":1}', "", "ok", ""]
        assert "bt_nus:~$ " not in lines

    def test_bridge_timeout_marker_raises_transport_error(self) -> None:
        # [Verificado 2026-08-13] Texto y fragmentación reales del puente
        # nRF52840 cuando su límite duro de 8000ms vence.
        fake = FakeBleakClient(ADDRESS)
        fake.script["STAT"] = [
            b"Error: sin respues",
            b"ta del modulo Qorvo (timeout)\r\n",
        ]
        transport, _ = make_transport(fake)

        with transport:
            transport.write_line("STAT")
            with pytest.raises(TransportError, match="timeout"):
                transport.read_line(0.5)

    def test_returns_none_on_timeout_without_data(self) -> None:
        transport, _ = make_transport()

        with transport:
            assert transport.read_line(0.1) is None

    def test_reconnects_when_bridge_drops_mid_command(self) -> None:
        # [Bug real, 2026-09-08, hardware real] El puente cierra la conexión
        # GATT mientras se espera la respuesta de un comando (comportamiento
        # normal de este puente); read_line() antes levantaba
        # "conexión BLE perdida esperando respuesta" y mataba la
        # calibración/validación en curso. Ahora reconecta y sigue esperando.
        fake = FakeBleakClient(ADDRESS)
        transport, client = make_transport(fake)

        with transport:
            client.simulate_disconnect()
            assert client._notify_callback is not None

            # La respuesta llega recién después de la reconexión (el módulo
            # Qorvo acumula notificaciones mientras el puente está caído).
            def deliver_backlog() -> None:
                assert client._notify_callback is not None
                client._notify_callback(None, bytearray(b"ok\r\n"))

            timer = threading.Timer(0.3, deliver_backlog)
            timer.start()
            try:
                line = transport.read_line(3.0)
                assert client.is_connected  # reconectado antes de salir del with
            finally:
                timer.join()

        assert line == "ok"

    def test_raises_after_failed_reconnect_attempts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Si la reconexión falla (puente apagado, fuera de alcance...), el
        # error se propaga en vez de colgar.
        monkeypatch.setattr(ble_link_module, "_CONNECT_RETRY_DELAY_S", 0.0)
        client = FakeBleakClient(ADDRESS)
        calls: list[int] = []

        def factory(
            address: str, disconnected_callback: Callable[[object], None] | None = None
        ) -> FakeBleakClient:
            calls.append(1)
            if len(calls) == 1:  # primera conexión (open)
                client._disconnected_callback = disconnected_callback
                return client
            return FakeBleakClient(ADDRESS, fail_connect=True)  # reconexión fallida

        transport = BleTransport(
            ADDRESS,
            power_on_settle_s=0.0,
            power_drain_s=0.05,
            connect_timeout_s=1.0,
            _client_factory=factory,
        )
        with transport:
            client.simulate_disconnect()
            with pytest.raises(TransportError):
                transport.read_line(2.0)
        # open + 1 read_line con _CONNECT_ATTEMPTS intentos de reconexión
        assert len(calls) == 1 + ble_link_module._CONNECT_ATTEMPTS

    def test_connect_retries_with_fresh_client_after_roe_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # [Bug real, 2026-09-08] Conectando, el objeto WinRT del cliente puede
        # estar cerrado todavía (RO_E_CLOSED: "[WinError -2147483629] Se cerró
        # el objeto"): hay que descartarlo y reintentar con un cliente nuevo.
        monkeypatch.setattr(ble_link_module, "_CONNECT_RETRY_DELAY_S", 0.0)
        calls: list[FakeBleakClient] = []

        class ClientClosedOnConnect(FakeBleakClient):
            async def connect(self) -> None:
                raise OSError(-2147483629, "Se cerró el objeto")

        def factory(
            address: str, disconnected_callback: Callable[[object], None] | None = None
        ) -> FakeBleakClient:
            client = ClientClosedOnConnect(address) if not calls else FakeBleakClient(address)
            client._disconnected_callback = disconnected_callback
            calls.append(client)
            return client

        transport = BleTransport(
            ADDRESS,
            power_on_settle_s=0.0,
            power_drain_s=0.05,
            connect_timeout_s=5.0,
            _client_factory=factory,
        )
        with transport:
            transport.write_line("STAT")
            assert transport._client is calls[1]
        assert len(calls) == 2  # el primer cliente falló y se usó uno nuevo

    def test_write_retries_with_fresh_client_after_roe_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # [Bug real, 2026-09-08] La escritura puede chocar con una caída GATT
        # que recién se procesa (RO_E_CLOSED en el write): reintentar con un
        # cliente nuevo en vez de propagar el WinError crudo.
        monkeypatch.setattr(ble_link_module, "_CONNECT_RETRY_DELAY_S", 0.0)
        calls: list[FakeBleakClient] = []

        class ClientDropsOnFirstWrite(FakeBleakClient):
            async def write_gatt_char(
                self, char_specifier: str, data: bytes, response: bool | None = None
            ) -> None:
                if not self.sent:  # primer write: la sesión acaba de morir
                    self.simulate_disconnect()
                    raise OSError(-2147483629, "Se cerró el objeto")
                await super().write_gatt_char(char_specifier, data, response)

        def factory(
            address: str, disconnected_callback: Callable[[object], None] | None = None
        ) -> FakeBleakClient:
            if calls:
                client = FakeBleakClient(address, script={"STAT": [b"mode: NONE\r\n", b"ok\r\n"]})
            else:
                client = ClientDropsOnFirstWrite(address)
            client._disconnected_callback = disconnected_callback
            calls.append(client)
            return client

        transport = BleTransport(
            ADDRESS,
            power_on_settle_s=0.0,
            power_drain_s=0.05,
            connect_timeout_s=5.0,
            _client_factory=factory,
        )
        with transport:
            transport.write_line("STAT")
            assert transport.read_line(2.0) == "mode: NONE"
            assert transport._client is calls[1]
        assert len(calls) == 2  # el primer cliente murió y se usó uno nuevo
        assert b"qorvo STAT\n" in calls[1].sent


class TestPower:
    def test_power_on_with_hold_formats_time_option(self) -> None:
        transport, client = make_transport()

        with transport:
            transport.power_on(hold_s=60)

        assert b"qorvo on -t 60s\n" in client.sent

    def test_power_off(self) -> None:
        transport, client = make_transport()

        with transport:
            transport.power_off()

        assert b"qorvo off\n" in client.sent
