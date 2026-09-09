"""Transportes simulados para tests sin hardware.

``FakeTransport`` implementa el protocolo ``Transport`` reproduciendo respuestas
del firmware a partir de un guion (comando → líneas de respuesta) y de una cola
de notificaciones espontáneas (para simular ``SESSION_INFO_NTF`` durante una
sesión de ranging).

``FakeBleakClient`` es el equivalente para ``BleTransport`` (rama
``hardware/ble-bridge-nrf52840``): implementa el subconjunto de la API de
``bleak.BleakClient`` que usa ``BleTransport`` (``_BleakClientLike``), sin
depender de Bluetooth real ni de la librería ``bleak``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable


class FakeTransport:
    """Simulación de una placa DWM3001CDK detrás de un puerto serie.

    Args:
        script: mapa de línea de comando exacta → líneas de respuesta. Si el
            comando enviado no figura, se busca por su primera palabra (útil
            para comandos con parámetros variables). Sin coincidencia, no se
            encola respuesta (simula silencio → timeout).
        notifications: líneas espontáneas que se entregan de a una cuando no
            hay respuestas pendientes (simula notificaciones de ranging).
    """

    def __init__(
        self,
        script: dict[str, list[str]] | None = None,
        notifications: Iterable[str] = (),
    ) -> None:
        self.script = dict(script or {})
        self.notifications: deque[str] = deque(notifications)
        self.sent: list[str] = []
        self.opened = False
        self._pending: deque[str] = deque()
        self._queued: dict[str, deque[list[str]]] = {}

    def queue_response(self, command: str, lines: list[str]) -> None:
        """Encola una respuesta de un solo uso para ``command``.

        Las respuestas encoladas tienen prioridad sobre el guion estático y se
        consumen en orden FIFO: permite simular comandos cuya respuesta cambia
        con el estado de la placa (p. ej. ``STAT`` antes y después de INITF).
        """
        self._queued.setdefault(command, deque()).append(list(lines))

    @property
    def name(self) -> str:
        return "FAKE"

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    def write_line(self, line: str) -> None:
        self.sent.append(line)
        queued = self._queued.get(line)
        if queued:
            self._pending.extend(queued.popleft())
            return
        response = self.script.get(line)
        if response is None:
            first_word = line.split(maxsplit=1)[0] if line.strip() else line
            response = self.script.get(first_word)
        if response is not None:
            self._pending.extend(response)

    def read_line(self, timeout_s: float) -> str | None:
        if self._pending:
            return self._pending.popleft()
        if self.notifications:
            return self.notifications.popleft()
        return None

    def read_notification_line(self, timeout_s: float) -> str | None:
        """Mismo canal que ``read_line`` (ver ``Transport.read_notification_line``);
        las subclases que sobrescriben ``read_line`` (p. ej. los simuladores TWR
        de ``test_calibration.py``) quedan cubiertas automáticamente al
        despachar por ``self``."""
        return self.read_line(timeout_s)

    def push_lines(self, lines: Iterable[str]) -> None:
        """Encola líneas arbitrarias como si llegaran de la placa."""
        self._pending.extend(lines)


# Duplicado a propósito (no se importa dwm3001c_cli.transport.ble_link acá):
# ese módulo importa `bleak` a nivel de módulo, y fakes.py lo usan también
# tests que no necesitan el extra `ble` instalado. Debe coincidir con
# ble_link.NUS_TX_CHAR_UUID / STREAM_DATA_CHAR_UUID.
_NUS_TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
_STREAM_DATA_CHAR_UUID = "36a9a2d9-a035-440f-8e59-ff0a72b2ba51"


class FakeBleakClient:
    """Doble de ``bleak.BleakClient`` para tests de ``BleTransport`` sin hardware.

    Args:
        address: dirección BLE (recibida igual que un ``BleakClient`` real).
        disconnected_callback: igual que en ``BleakClient``.
        script: mapa de texto de comando **sin** el prefijo ``"qorvo "`` ni el
            ``\\n`` final → lista de fragmentos ``bytes`` a entregar como
            notificaciones separadas por la característica de comandos (NUS
            TX) — para simular la fragmentación arbitraria real de las
            notificaciones BLE. Para simular datos del canal de streaming
            dedicado, ver :meth:`simulate_stream_data`.
        mtu_size: valor fijo a reportar en ``mtu_size``.
        fail_connect: si es ``True``, ``connect()`` lanza ``BleakError``.
        gatt_char_values: mapa característica → valor fijo para
            ``read_gatt_char`` (p. ej. batería/versión de firmware del
            puente); una característica no listada lanza ``BleakError``,
            igual que un dispositivo real sin ese servicio.
    """

    def __init__(
        self,
        address: str,
        disconnected_callback: Callable[[FakeBleakClient], None] | None = None,
        services: Iterable[str] | None = None,
        *,
        winrt: dict[str, object] | None = None,
        script: dict[str, list[bytes]] | None = None,
        mtu_size: int = 247,
        fail_connect: bool = False,
        gatt_char_values: dict[str, bytes] | None = None,
    ) -> None:
        self.address = address
        self._disconnected_callback = disconnected_callback
        self._connected = False
        self._notify_callbacks: dict[str, Callable[[object, bytearray], None]] = {}
        self.script = dict(script or {})
        self.mtu_size = mtu_size
        self.fail_connect = fail_connect
        self.sent: list[bytes] = []
        self.gatt_char_values = dict(gatt_char_values or {})
        # Para que los tests puedan verificar con qué opciones se construyó
        # este cliente (p. ej. si BleTransport pidió el caché de servicios).
        self.requested_services = list(services) if services is not None else None
        self.winrt_args = dict(winrt or {})

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def _notify_callback(self) -> Callable[[object, bytearray], None] | None:
        """Compat: alias de conveniencia al callback de la característica de
        comandos (NUS TX) — la mayoría de los tests existentes simulan
        tráfico en ese único canal, de antes de que existiera el canal de
        streaming dedicado. Para simular datos de ese canal nuevo, usar
        :meth:`simulate_stream_data`."""
        return self._notify_callbacks.get(_NUS_TX_CHAR_UUID)

    async def connect(self) -> None:
        from bleak.exc import BleakError

        if self.fail_connect:
            raise BleakError("fake: fallo de conexión simulado")
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def start_notify(
        self, char_specifier: str, callback: Callable[[object, bytearray], None]
    ) -> None:
        self._notify_callbacks[char_specifier] = callback

    async def stop_notify(self, char_specifier: str) -> None:
        self._notify_callbacks.pop(char_specifier, None)

    async def write_gatt_char(
        self, char_specifier: str, data: bytes, response: bool | None = None
    ) -> None:
        self.sent.append(bytes(data))
        text = bytes(data).decode("ascii").rstrip("\n")
        assert text.startswith("qorvo "), f"se esperaba el prefijo 'qorvo ': {text!r}"
        command = text[len("qorvo ") :]
        chunks = self.script.get(command)
        callback = self._notify_callbacks.get(_NUS_TX_CHAR_UUID)
        if chunks and callback is not None:
            for chunk in chunks:
                callback(None, bytearray(chunk))

    async def read_gatt_char(self, char_specifier: str) -> bytearray:
        from bleak.exc import BleakError

        value = self.gatt_char_values.get(char_specifier)
        if value is None:
            raise BleakError(f"fake: caracteristica desconocida {char_specifier}")
        return bytearray(value)

    def simulate_stream_data(self, chunk: bytes) -> None:
        """Simula datos entrantes por la característica dedicada de streaming
        (``STREAM_DATA_CHAR_UUID``), separada del canal de comandos."""
        callback = self._notify_callbacks.get(_STREAM_DATA_CHAR_UUID)
        assert callback is not None, "no suscripto a la característica de streaming"
        callback(None, bytearray(chunk))

    def simulate_disconnect(self) -> None:
        """Simula un corte de conexión espontáneo (ej. el timeout de inactividad real)."""
        self._connected = False
        if self._disconnected_callback is not None:
            self._disconnected_callback(self)
