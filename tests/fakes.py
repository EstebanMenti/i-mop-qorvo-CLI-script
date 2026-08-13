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

    def push_lines(self, lines: Iterable[str]) -> None:
        """Encola líneas arbitrarias como si llegaran de la placa."""
        self._pending.extend(lines)


class FakeBleakClient:
    """Doble de ``bleak.BleakClient`` para tests de ``BleTransport`` sin hardware.

    Args:
        address: dirección BLE (recibida igual que un ``BleakClient`` real).
        disconnected_callback: igual que en ``BleakClient``.
        script: mapa de texto de comando **sin** el prefijo ``"qorvo "`` ni el
            ``\\n`` final → lista de fragmentos ``bytes`` a entregar como
            notificaciones separadas (para simular la fragmentación arbitraria
            real de las notificaciones BLE).
        mtu_size: valor fijo a reportar en ``mtu_size``.
        fail_connect: si es ``True``, ``connect()`` lanza ``BleakError``.
    """

    def __init__(
        self,
        address: str,
        disconnected_callback: Callable[[FakeBleakClient], None] | None = None,
        *,
        script: dict[str, list[bytes]] | None = None,
        mtu_size: int = 247,
        fail_connect: bool = False,
    ) -> None:
        self.address = address
        self._disconnected_callback = disconnected_callback
        self._connected = False
        self._notify_callback: Callable[[object, bytearray], None] | None = None
        self.script = dict(script or {})
        self.mtu_size = mtu_size
        self.fail_connect = fail_connect
        self.sent: list[bytes] = []

    @property
    def is_connected(self) -> bool:
        return self._connected

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
        self._notify_callback = callback

    async def stop_notify(self, char_specifier: str) -> None:
        self._notify_callback = None

    async def write_gatt_char(
        self, char_specifier: str, data: bytes, response: bool | None = None
    ) -> None:
        self.sent.append(bytes(data))
        text = bytes(data).decode("ascii").rstrip("\n")
        assert text.startswith("qorvo "), f"se esperaba el prefijo 'qorvo ': {text!r}"
        command = text[len("qorvo ") :]
        chunks = self.script.get(command)
        if chunks and self._notify_callback is not None:
            for chunk in chunks:
                self._notify_callback(None, bytearray(chunk))

    def simulate_disconnect(self) -> None:
        """Simula un corte de conexión espontáneo (ej. el timeout de inactividad real)."""
        self._connected = False
        if self._disconnected_callback is not None:
            self._disconnected_callback(self)
