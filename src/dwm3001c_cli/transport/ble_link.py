"""Transporte Bluetooth Low Energy: ``BleTransport`` sobre el puente nRF52840.

Implementa el mismo ``Transport`` que ``SerialLink`` (ver ``serial_link.py``),
así que ``DwmCliClient`` y todo lo de ``calibration``/``validation`` lo usan
sin ningún cambio. Solo se usa en la rama ``hardware/ble-bridge-nrf52840``
(ver ``docs/rama-hardware-ble.md``) — no es parte del flujo USB-USB de `main`.

Protocolo (Nordic UART Service, firmware puente ya implementado en el repo
hermano ``I-mop-nrf52840-fw``): cada línea de comando se envía como
``qorvo <línea>\\n`` por la característica RX; el puente reenvía el texto tal
cual por UART al Qorvo y retransmite su respuesta cruda por la característica
TX — el formato de línea que ve ``core/parsers.py`` es el mismo que por USB.

Hallazgos verificados contra hardware real (2026-08-13, smoke test propio con
``bleak``, ver ``docs/rama-hardware-ble.md`` §7.1/§8), no solo documentación
del repo hermano:

- MTU negociado 247 en Windows/WinRT; sin truncado en respuestas largas.
- Pairing Just Works sin diálogo de Windows.
- Las notificaciones BLE llegan fragmentadas en cualquier punto (no alineadas
  a líneas) — se reensamblan con el mismo ``LineAssembler`` que ``SerialLink``.
- El shell de Zephyr intercala un prompt literal (``bt_nus:~$ ``) al final de
  cada respuesta, no documentado en la especificación original del puente —
  se filtra antes de encolar líneas.
- La conexión BLE se cierra sola ~7-8 s después de la última actividad
  (confirmado con y sin error del puente) — comportamiento normal de este
  puente, no un fallo. Por eso, a diferencia de ``SerialLink`` (que nunca
  reconecta solo), ``write_line`` reconecta automáticamente si hace falta.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import re
import threading
import time
from collections.abc import Callable, Coroutine
from concurrent.futures import TimeoutError as FutureTimeoutError
from types import TracebackType
from typing import Any, Protocol, Self, cast

from bleak import BleakClient
from bleak.exc import BleakError

from dwm3001c_cli.core.errors import TransportError
from dwm3001c_cli.transport.serial_link import LineAssembler

logger = logging.getLogger(__name__)

NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # PC -> nRF (write)
NUS_TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # nRF -> PC (notify)

# Prompt del shell de Zephyr tras cada respuesta (ej. "bt_nus:~$ "); no es
# contenido del Qorvo, hay que descartarlo antes de que lo vea DwmCliClient.
_PROMPT_RE = re.compile(r"^\S*:~\$\s*$")

# [Verificado 2026-08-13] Texto real emitido por el puente cuando su límite
# duro de 8000 ms vence sin respuesta del Qorvo (ver referencia en el repo
# hermano I-mop-nrf52840-fw). Se compara por prefijo, no exacto, porque llega
# fragmentado en varias notificaciones y el firmware del puente podría variar
# ligeramente el resto del mensaje entre versiones.
_BRIDGE_TIMEOUT_MARKER = "Error: sin respuesta del modulo Qorvo"

# [Verificado 2026-08-13] Tiempo de arranque del Qorvo tras "qorvo on" antes
# de que responda de forma confiable (probado con 3.0s; sin espera, sin
# probar). Ver docs/rama-hardware-ble.md §7.1.
_POWER_ON_SETTLE_S = 3.0


class _BleakClientLike(Protocol):
    """Subconjunto de la API de ``BleakClient`` que usa ``BleTransport``.

    Permite inyectar un doble de prueba (``tests/fakes.py``) sin depender de
    ``bleak`` en los tests que no necesitan hardware ni el backend real.
    """

    @property
    def is_connected(self) -> bool: ...

    @property
    def mtu_size(self) -> int: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def start_notify(self, char_specifier: str, callback: object) -> None: ...

    async def stop_notify(self, char_specifier: str) -> None: ...

    async def write_gatt_char(
        self, char_specifier: str, data: bytes, response: bool | None = None
    ) -> None: ...


class BleTransport:
    """Transporte ``Transport`` sobre el puente Bluetooth nRF52840 (NUS).

    Uso típico, igual que ``SerialLink``::

        with BleTransport("FD:7A:90:57:CC:9F") as link:
            link.write_line("STAT")
            line = link.read_line(timeout_s=10.0)

    Corre un hilo dedicado con su propio event loop de asyncio (requisito del
    backend WinRT de ``bleak``: todas las llamadas de una misma conexión deben
    hacerse desde el mismo hilo); los métodos públicos son síncronos y
    despachan corutinas a ese hilo.

    Args:
        address: dirección BLE del puente (ver ``dwm ble-scan``).
        connect_timeout_s: tiempo máximo para conectar (incluye negociación
            de MTU y pairing; se midió hasta ~9 s en la primera conexión).
        write_timeout_s: tiempo máximo para que se complete una escritura GATT.
        power_on_settle_s: espera tras encender el módulo Qorvo en ``open()``.
    """

    NUS_SERVICE_UUID = NUS_SERVICE_UUID
    NUS_RX_CHAR_UUID = NUS_RX_CHAR_UUID
    NUS_TX_CHAR_UUID = NUS_TX_CHAR_UUID

    def __init__(
        self,
        address: str,
        *,
        connect_timeout_s: float = 20.0,
        write_timeout_s: float = 5.0,
        power_on_settle_s: float = _POWER_ON_SETTLE_S,
        power_drain_s: float = 2.0,
        _client_factory: Callable[..., _BleakClientLike] | None = None,
    ) -> None:
        self._address = address
        # calibration/autocal.py y validation/report.py arman nombres de
        # archivo con Transport.name (p. ej. "calibracion-{name}-fecha.json");
        # una dirección BLE trae ":" (MAC), inválido en nombres de archivo de
        # Windows — se lo reemplaza acá, no en cada consumidor.
        self._name = f"BLE-{address.replace(':', '')}"
        self._connect_timeout_s = connect_timeout_s
        self._write_timeout_s = write_timeout_s
        self._power_on_settle_s = power_on_settle_s
        self._power_drain_s = power_drain_s
        self._client_factory: Callable[..., _BleakClientLike] = _client_factory or cast(
            "Callable[..., _BleakClientLike]", BleakClient
        )
        self._client: _BleakClientLike | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._assembler = LineAssembler()
        self._rx_queue: queue.Queue[str] = queue.Queue()
        self._pending_error: str | None = None
        # [Bug real, verificado 2026-08-25 contra hardware real] Copias planas
        # de estado, actualizadas solo desde el hilo dedicado de bleak
        # (self._thread) — nunca leer self._client.is_connected/.mtu_size
        # directamente desde otro hilo (p. ej. el QThread de la GUI que llama
        # read_line()): es un objeto COM/WinRT con afinidad de hilo, y
        # tocarlo desde otro hilo crashea el proceso entero sin ninguna traza
        # de Python — confirmado aislando el problema contra hardware real.
        self._connected = False
        self._mtu_size: int | None = None

    @property
    def name(self) -> str:
        return self._name

    # ------------------------------------------------------------- ciclo de vida

    def open(self) -> None:
        """Conecta, habilita notificaciones y enciende el módulo Qorvo (``qorvo on``)."""
        if self._thread is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name=f"ble-{self._address}", daemon=True
        )
        self._thread.start()
        self._run_coro(self._connect(), timeout_s=self._connect_timeout_s)
        self.power_on()
        time.sleep(self._power_on_settle_s)

    def close(self) -> None:
        if self._loop is None:
            return
        try:
            self._run_coro(self._disconnect(), timeout_s=5.0)
        except TransportError:
            logger.warning("%s: fallo al desconectar limpiamente", self.name, exc_info=True)
        finally:
            loop = self._loop
            loop.call_soon_threadsafe(loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5.0)
            self._loop = None
            self._thread = None
            self._client = None

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --------------------------------------------------------------- Transport

    def write_line(self, line: str) -> None:
        self._ensure_connected()
        self._reset_pending()
        self._run_coro(self._send_raw(line), timeout_s=self._write_timeout_s)

    def _reset_pending(self) -> None:
        """Descarta cualquier fragmento/línea que haya quedado de la respuesta
        anterior antes de mandar un comando nuevo.

        [Bug real, 2026-08-13] Las notificaciones BLE (característica NUS TX,
        modo "Notify") **no tienen ACK ni retransmisión a nivel GATT**: una
        notificación perdida es normal y posible. Cuando la línea que
        contenía el ``\\n`` de cierre es justo la que se pierde, el fragmento
        parcial queda indefinidamente en el buffer del ``LineAssembler`` —
        confirmado con hardware real: un ``CALKEY <clave> <valor>`` se quedó
        sin respuesta 30s, y el eco truncado (``CALKEY <clave>``, sin el
        valor ni el terminador) reapareció recién cuando un comando
        *completamente distinto*, minutos después, aportó el ``\\n`` que le
        faltaba — produciendo una línea mezclada sin sentido que rompió el
        parseo del comando siguiente. No se puede recuperar el dato perdido,
        pero si se limpia el buffer antes de cada comando nuevo, lo peor que
        pasa es un timeout honesto en el comando que perdió su notificación,
        en vez de corromper silenciosamente la respuesta de otro comando.
        """
        while True:
            try:
                self._rx_queue.get_nowait()
            except queue.Empty:
                break
        self._assembler = LineAssembler()
        self._pending_error = None

    def read_line(self, timeout_s: float) -> str | None:
        """Devuelve la próxima línea, o ``None`` si venció ``timeout_s``.

        Sondea en pasos cortos (no un único ``queue.get`` bloqueante) para
        poder detectar una desconexión o un timeout del puente mientras se
        espera, en vez de esperar el ``timeout_s`` completo a ciegas.
        """
        deadline = time.monotonic() + timeout_s
        poll_s = 0.05
        while True:
            if self._pending_error is not None:
                error = self._pending_error
                self._pending_error = None
                raise TransportError(f"{self.name}: {error}")
            remaining = deadline - time.monotonic()
            try:
                return self._rx_queue.get(timeout=min(poll_s, max(0.0, remaining)))
            except queue.Empty:
                pass
            if self._client is not None and not self._connected:
                raise TransportError(f"{self.name}: conexión BLE perdida esperando respuesta")
            if time.monotonic() >= deadline:
                return None

    # --------------------------------------------------- extensiones propias BLE

    def power_on(self, hold_s: float | None = None) -> None:
        """``qorvo on``: enciende el módulo Qorvo (fuera del contrato ``Transport``).

        No es un comando de la CLI del Qorvo, es una palabra reservada del
        firmware puente que controla el GPIO de alimentación del módulo — por
        eso no pasa por ``write_line`` (que sería indistinguible de un comando
        real reenviado al Qorvo).
        """
        text = "on" if hold_s is None else f"on -t {hold_s:g}s"
        self._ensure_connected()
        self._run_coro(self._send_raw(text), timeout_s=self._write_timeout_s)
        self._drain_response()

    def power_off(self, hold_s: float | None = None) -> None:
        """``qorvo off``: apaga el módulo Qorvo (ver :meth:`power_on`)."""
        text = "off" if hold_s is None else f"off -t {hold_s:g}s"
        self._ensure_connected()
        self._run_coro(self._send_raw(text), timeout_s=self._write_timeout_s)
        self._drain_response()

    def _drain_response(self, quiet_s: float | None = None) -> None:
        """Lee y descarta hasta que no llegue nada nuevo por ``quiet_s``.

        [Bug real, 2026-08-13] ``qorvo on``/``qorvo off`` no tienen un
        marcador de fin de respuesta tipo ``ok`` (el firmware puente solo
        manda ``"Qorvo status changed to: ..."`` y el prompt del shell, que ya
        se filtra en ``_on_notify``). Sin drenar acá, esa línea quedaba en la
        cola y el siguiente comando real (p. ej. ``STOP``) la tomaba como si
        fuera su propia respuesta — confirmado contra hardware real: rompió
        el parseo de ``STAT`` al confundir la respuesta de ``STOP`` con la de
        ``qorvo on``.
        """
        effective_quiet_s = quiet_s if quiet_s is not None else self._power_drain_s
        while True:
            line = self.read_line(effective_quiet_s)
            if line is None:
                return
            logger.debug("%s: descartada tras encender/apagar: %r", self.name, line)

    @property
    def mtu_size(self) -> int | None:
        return self._mtu_size

    # ---------------------------------------------------------------- internos

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_coro(self, coro: Coroutine[Any, Any, None], *, timeout_s: float) -> None:
        if self._loop is None:
            raise TransportError(f"{self.name}: transporte no abierto (llamar open() primero)")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            future.result(timeout_s)
        except FutureTimeoutError as exc:
            raise TransportError(f"{self.name}: timeout esperando una operación BLE") from exc
        except BleakError as exc:
            raise TransportError(f"{self.name}: error BLE: {exc}") from exc

    def _ensure_connected(self) -> None:
        if self._loop is None:
            raise TransportError(f"{self.name}: transporte no abierto (llamar open() primero)")
        if self._client is not None and self._connected:
            return
        # [Verificado 2026-08-13] La conexión se cierra sola ~7-8s después de
        # la última actividad; no es un error, es el comportamiento normal de
        # este puente. Reconectar acá (a diferencia de SerialLink, que nunca
        # reconecta solo) es deliberado — ver docs/rama-hardware-ble.md §8.
        logger.warning("%s: reconectando (conexión BLE inactiva o caída)", self.name)
        self._run_coro(self._connect(), timeout_s=self._connect_timeout_s)

    async def _connect(self) -> None:
        client = self._client_factory(self._address, disconnected_callback=self._on_disconnect)
        await client.connect()
        await client.start_notify(NUS_TX_CHAR_UUID, self._on_notify)
        self._client = client
        self._connected = True
        self._mtu_size = client.mtu_size
        logger.debug("%s: conectado, MTU=%s", self.name, self._mtu_size)

    async def _disconnect(self) -> None:
        if self._client is None:
            return
        try:
            if self._connected:
                await self._client.stop_notify(NUS_TX_CHAR_UUID)
                await self._client.disconnect()
        finally:
            self._client = None
            self._connected = False
            self._mtu_size = None

    async def _send_raw(self, text: str) -> None:
        if self._client is None:
            raise TransportError(f"{self.name}: no conectado")
        payload = f"qorvo {text}\n".encode("ascii")
        logger.debug("TX %s: %s", self.name, payload)
        await self._client.write_gatt_char(NUS_RX_CHAR_UUID, payload, response=False)

    def _on_disconnect(self, _client: object) -> None:
        # Corre en el hilo dedicado de bleak (self._thread), como todo lo que
        # toca self._client — seguro escribir acá el mismo atributo plano que
        # lee read_line() desde cualquier otro hilo.
        self._connected = False
        logger.warning("%s: conexión BLE cerrada", self.name)

    def _on_notify(self, _sender: object, data: bytearray) -> None:
        for line in self._assembler.feed(bytes(data)):
            if _PROMPT_RE.match(line):
                logger.debug("%s: prompt de shell descartado: %r", self.name, line)
                continue
            if line.startswith(_BRIDGE_TIMEOUT_MARKER):
                logger.warning("%s: el puente reportó timeout hacia el Qorvo: %s", self.name, line)
                self._pending_error = line
                continue
            logger.debug("RX %s: %s", self.name, line)
            self._rx_queue.put(line)
