"""Tests de BleTransport (rama hardware/ble-bridge-nrf52840), sin hardware."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

import pytest
from bleak.exc import BleakError

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
        address: str,
        disconnected_callback: Callable[[object], None] | None = None,
        services: object = None,
        *,
        winrt: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> FakeBleakClient:
        client._disconnected_callback = disconnected_callback
        client.requested_services = list(services) if services is not None else None  # type: ignore[arg-type]
        client.winrt_args = dict(winrt or {})
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

    def test_first_connect_requests_scoped_service_and_cache(self) -> None:
        # [Mitigación 2026-09-09] Reduce el tiempo muerto de una reconexión:
        # limitar el descubrimiento a los servicios usados (NUS + streaming)
        # y pedirle a Windows que reuse su caché de servicios ya conocido.
        transport, client = make_transport()

        with transport:
            assert client.requested_services == [
                ble_link_module.NUS_SERVICE_UUID,
                ble_link_module.STREAM_SERVICE_UUID,
            ]
            assert client.winrt_args == {"use_cached_services": True}

    def test_connect_falls_back_to_uncached_services_after_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # El usuario pidió explícitamente que, si el camino rápido (caché de
        # servicios de Windows) falla, la conexión igual se complete —
        # prefiriendo una reconexión más lenta (sin caché, redescubriendo
        # todo el GATT) a que la optimización bloquee el proceso.
        monkeypatch.setattr(ble_link_module, "_CONNECT_RETRY_DELAY_S", 0.0)
        calls: list[FakeBleakClient] = []

        class FailsWithCachedServices(FakeBleakClient):
            async def connect(self) -> None:
                if self.winrt_args.get("use_cached_services"):
                    raise BleakError("fake: caché de servicios desactualizado")
                await super().connect()

        def factory(
            address: str,
            disconnected_callback: Callable[[object], None] | None = None,
            **kwargs: object,
        ) -> FakeBleakClient:
            client = FailsWithCachedServices(address, **kwargs)  # type: ignore[arg-type]
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
            assert transport._client is calls[-1]
            assert calls[-1].is_connected

        # El primer intento pidió caché y falló; el siguiente lo desactivó y
        # conectó con éxito — el proceso terminó conectando, no se bloqueó.
        assert len(calls) >= 2
        assert calls[0].winrt_args == {"use_cached_services": True}
        assert calls[-1].winrt_args == {"use_cached_services": False}


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
            address: str,
            disconnected_callback: Callable[[object], None] | None = None,
            **_kwargs: object,
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
            address: str,
            disconnected_callback: Callable[[object], None] | None = None,
            **_kwargs: object,
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
            address: str,
            disconnected_callback: Callable[[object], None] | None = None,
            **_kwargs: object,
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

    def test_write_retry_survives_hanging_disconnect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # [Bug real, 2026-09-08] Tras una caída GATT, disconnect() del backend
        # WinRT puede quedar colgado: el descarte del cliente no debe colgar
        # ni matar el reintento de escritura ("timeout esperando una
        # operación BLE").
        monkeypatch.setattr(ble_link_module, "_CONNECT_RETRY_DELAY_S", 0.0)
        monkeypatch.setattr(ble_link_module, "_DISCONNECT_TIMEOUT_S", 0.05)
        calls: list[FakeBleakClient] = []

        class ClientHangsOnDisconnect(FakeBleakClient):
            async def write_gatt_char(
                self, char_specifier: str, data: bytes, response: bool | None = None
            ) -> None:
                if not self.sent:  # primer write: la sesión acaba de morir
                    self.simulate_disconnect()
                    raise OSError(-2147483629, "Se cerró el objeto")
                await super().write_gatt_char(char_specifier, data, response)

            async def disconnect(self) -> None:
                await asyncio.sleep(3600)  # cuelga como el WinRT real

        def factory(
            address: str,
            disconnected_callback: Callable[[object], None] | None = None,
            **_kwargs: object,
        ) -> FakeBleakClient:
            if calls:
                client = FakeBleakClient(address, script={"STAT": [b"mode: NONE\r\n", b"ok\r\n"]})
            else:
                client = ClientHangsOnDisconnect(address)
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
        assert len(calls) == 2  # el primer cliente (colgado) se descartó y se usó uno nuevo
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


class TestStreaming:
    """Canal BLE dedicado de streaming (``STREAM_SERVICE_UUID`` /
    ``STREAM_DATA_CHAR_UUID``), separado del canal de comandos (NUS TX) —
    ver docstring de ``STREAM_SERVICE_UUID`` en ``ble_link.py`` para el
    motivo (el canal de comandos suspendía el UART tras 8s de ranging
    continuo, sin este canal dedicado)."""

    def test_open_subscribes_to_stream_and_enables_it(self) -> None:
        transport, client = make_transport()

        with transport:
            assert ble_link_module.STREAM_DATA_CHAR_UUID in client._notify_callbacks
            assert b"qorvo stream on\n" in client.sent

    def test_read_notification_line_reads_from_dedicated_stream_channel(self) -> None:
        transport, client = make_transport()

        with transport:
            client.simulate_stream_data(
                b"SESSION_INFO_NTF: {session_handle=1, sequence_number=0, block_index=0,"
                b' n_measurements=1 [mac_address=0x0001, status="SUCCESS", distance[cm]=200]}\r\n'
            )
            assert transport.read_notification_line(0.2) == (
                "SESSION_INFO_NTF: {session_handle=1, sequence_number=0, block_index=0,"
                ' n_measurements=1 [mac_address=0x0001, status="SUCCESS", distance[cm]=200]}'
            )

    def test_stream_data_never_reaches_command_channel(self) -> None:
        # El motivo de tener dos colas separadas: un STAT de keepalive
        # durante el muestreo no debe comerse (ni contaminarse con)
        # notificaciones de ranging en curso, y viceversa.
        transport, client = make_transport()

        with transport:
            client.simulate_stream_data(b"SESSION_INFO_NTF: {algo}\r\n")
            assert transport.read_line(0.2) is None
            assert transport.read_notification_line(0.2) == "SESSION_INFO_NTF: {algo}"

    def test_command_response_never_reaches_stream_channel(self) -> None:
        fake = FakeBleakClient(ADDRESS, script={"STAT": [b"mode: NONE\r\nok\r\n"]})
        transport, _ = make_transport(fake)

        with transport:
            transport.write_line("STAT")
            assert transport.read_line(0.2) == "mode: NONE"
            assert transport.read_notification_line(0.1) is None

    def test_reenables_stream_after_automatic_reconnect(self) -> None:
        # [Bug real, 2026-09-09, hardware real] El streaming se apaga solo
        # al desconectarse el BLE (a diferencia del encendido físico del
        # Qorvo, que es un GPIO persistente) — a diferencia de power_on(),
        # que open() solo manda una vez. Antes de este fix, una reconexión
        # automática (p. ej. el timeout de inactividad de ~7-8s cayendo
        # justo antes de arrancar el ranging) dejaba el streaming apagado
        # sin que nada lo notara: confirmado contra hardware real, GUI
        # real, "0 notificaciones recibidas en 100s" con el enlace BLE sano
        # el resto del tiempo.
        fake = FakeBleakClient(ADDRESS)
        transport, client = make_transport(fake)

        with transport:
            stream_on_before = client.sent.count(b"qorvo stream on\n")
            client.simulate_disconnect()

            transport.write_line("STAT")  # dispara la reconexión automática

            assert client.sent.count(b"qorvo stream on\n") == stream_on_before + 1

    def test_reconnect_does_not_swallow_pending_command_response(self) -> None:
        """[Bug real #2, 2026-09-09, hardware real] Reenviar "stream on" al
        reconectar (ver test anterior) no debe drenar ``self._rx_queue``: si
        lo hace, se come la respuesta real de un comando que ya estaba
        pendiente antes del corte. Confirmado con hardware real: un ``INITF``
        completo (más una ráfaga de ``SESSION_INFO_NTF`` de la sesión de
        ranging que había seguido activa en el firmware durante el corte, y
        que temporalmente sale por el canal de comandos hasta que el
        firmware procesa este mismo "stream on") desapareció así, línea por
        línea, y el comando terminó en un timeout de 10s sin ninguna pista.
        """
        fake = FakeBleakClient(ADDRESS)
        transport, client = make_transport(fake)

        with transport:
            client.simulate_disconnect()
            # La respuesta real del comando pendiente (p. ej. el eco de
            # INITF) ya está en la cola cuando se dispara la reconexión —
            # simula la ráfaga que el firmware entrega de una sola vez al
            # reconectar, antes de que el llamador original la lea.
            transport._rx_queue.put("ok")

            transport._ensure_connected()

            assert transport._rx_queue.get_nowait() == "ok"
        assert b"qorvo stream on\n" in client.sent
