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
from typing import Any, Protocol, Self, TypeVar, cast

from bleak import BleakClient
from bleak.exc import BleakError

from dwm3001c_cli.core.errors import TransportError
from dwm3001c_cli.transport.serial_link import LineAssembler

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # PC -> nRF (write)
NUS_TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # nRF -> PC (notify)

# [Agregado 2026-09-09, firmware del puente actualizado] Servicio "Qorvo
# Stream" (doc/00_BLE_Protocol_Specification.md §5.4/§7.7 de
# I-mop-nrf52840-fw): canal BLE dedicado, solo Notify, para streaming
# continuo de SESSION_INFO_NTF durante una sesión de ranging activa —
# reemplaza depender de NUS TX para esto. Motivo: el canal de comandos
# shell-NUS (``qorvo <cmd>``) es petición/respuesta con una ventana acotada
# (silencio 400ms / timeout duro 8000ms) y, al vencer esa ventana, el puente
# suspendía el UART hacia el Qorvo incondicionalmente — con SESSION_INFO_NTF
# llegando cada ~200ms durante el ranging, el silencio nunca se cumplía, así
# que la ventana corría siempre hasta los 8000ms y todo lo que el Qorvo
# transmitía después se perdía (la ISR de recepción quedaba deshabilitada)
# hasta el próximo comando — confirmado contra hardware real: ráfagas de
# ~40 notificaciones (8000ms / 200ms) y silencio total después, sin ninguna
# desconexión BLE de por medio. Activado con ``enable_stream()`` (comando
# reservado ``qorvo stream on``, igual mecanismo que ``power_on()``).
STREAM_SERVICE_UUID = "019dad38-2b03-4df9-ac87-70ce530540fb"
STREAM_DATA_CHAR_UUID = "36a9a2d9-a035-440f-8e59-ff0a72b2ba51"  # nRF -> PC (notify)

# Servicios estándar de Bluetooth SIG que expone el puente (ver
# I-mop-nrf52840-fw/doc/00_BLE_Protocol_Specification.md §5.1/§5.2) — nada
# propietario, UUIDs de 16 bits sobre la base estándar de Bluetooth. Battery
# Level es el nivel de batería del puente (0-100%); Firmware Revision es la
# versión del firmware del puente nRF52840 (no la del Qorvo — para eso ver
# DwmCliClient.stat(), campo "Version"/"Build").
BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_CHAR_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
DEVICE_INFO_SERVICE_UUID = "0000180a-0000-1000-8000-00805f9b34fb"
FIRMWARE_REV_CHAR_UUID = "00002a26-0000-1000-8000-00805f9b34fb"

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


# Reconexiones transparentes máximas dentro de una sola llamada a
# read_line(): si la conexión sigue cayendo persistentemente, se propaga
# el error en vez de extender el timeout indefinidamente.
_MAX_READ_RECONNECTS = 3

# [Bug real, 2026-09-08] Cuando cae la sesión GATT, el objeto WinRT del
# cliente viejo queda cerrado (RO_E_CLOSED: "[WinError -2147483629] Se cerró
# el objeto") y, hasta que Windows termina de liberar la sesión, hasta la
# conexión nueva puede fallar. Respuesta: descartar SIEMPRE el cliente y
# crear uno nuevo, con reintentos acotados tanto al conectar como al escribir.
_CONNECT_ATTEMPTS = 3
_CONNECT_RETRY_DELAY_S = 1.0
_WRITE_ATTEMPTS = 3
# Presupuesto máximo para desconectar un cliente: tras una caída GATT,
# disconnect() del backend WinRT puede quedar colgado esperando eventos de
# una sesión ya muerta — sin tope, bloquearía write_line/read_line.
_DISCONNECT_TIMEOUT_S = 5.0


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

    async def read_gatt_char(self, char_specifier: str) -> bytearray: ...


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
        # Canal separado para el streaming de ranging (característica
        # "Qorvo Stream Data"): nunca comparte cola con las respuestas de
        # comando (_rx_queue), para que un STAT de keepalive no se coma (ni
        # contamine) notificaciones SESSION_INFO_NTF en curso, ni viceversa.
        self._stream_assembler = LineAssembler()
        self._stream_queue: queue.Queue[str] = queue.Queue()
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
        # Último valor leído por read_battery_level()/read_bridge_firmware_version()
        # (ver esos métodos) — None hasta la primera lectura exitosa.
        self.battery_pct: int | None = None
        self.bridge_firmware_version: str | None = None

    @property
    def name(self) -> str:
        return self._name

    # ------------------------------------------------------------- ciclo de vida

    def open(self) -> None:
        """Conecta, habilita notificaciones, enciende el módulo Qorvo (``qorvo
        on``) y activa el streaming continuo de ranging (``qorvo stream on``,
        ver :meth:`enable_stream`)."""
        if self._thread is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name=f"ble-{self._address}", daemon=True
        )
        self._thread.start()
        # El presupuesto debe cubrir todos los intentos internos de _connect()
        # (cada connect() de bleak puede tardar hasta ~10 s en Windows).
        self._run_coro(self._connect(), timeout_s=self._connect_timeout_s * _CONNECT_ATTEMPTS)
        self.power_on()
        time.sleep(self._power_on_settle_s)
        # El Qorvo debe estar encendido antes de aceptar el comando (mismo
        # precondición que cualquier otro `qorvo <cmd>`, ver power_on()).
        self.enable_stream()

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
        self._reset_pending()
        self._send_with_retry(line)

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
        """Devuelve la próxima línea de respuesta de comando, o ``None`` si
        venció ``timeout_s``. Ver :meth:`_read_from_queue`."""
        return self._read_from_queue(self._rx_queue, timeout_s, what="respuesta")

    def read_notification_line(self, timeout_s: float) -> str | None:
        """Devuelve la próxima línea del canal de streaming BLE dedicado
        (característica "Qorvo Stream Data", ver :data:`STREAM_DATA_CHAR_UUID`
        y :meth:`enable_stream`), o ``None`` si venció ``timeout_s``. Ver
        :meth:`_read_from_queue`.
        """
        return self._read_from_queue(self._stream_queue, timeout_s, what="datos de streaming")

    def _read_from_queue(
        self, source: queue.Queue[str], timeout_s: float, *, what: str
    ) -> str | None:
        """Lógica de lectura compartida por ``read_line`` y
        ``read_notification_line``: mismo criterio de reconexión tolerante,
        cada una sobre su propia cola (``_rx_queue``/``_stream_queue`` — nunca
        se mezclan, ver comentario en ``__init__``).

        Sondea en pasos cortos (no un único ``queue.get`` bloqueante) para
        poder detectar una desconexión o un timeout del puente mientras se
        espera, en vez de esperar el ``timeout_s`` completo a ciegas.

        [Bug real, 2026-09-08] El puente puede cerrar la conexión GATT en el
        medio de un comando (comportamiento normal de este puente, ver
        ``_ensure_connected``); levantar ``TransportError`` acá mataba la
        calibración/validación en curso ("conexión BLE perdida esperando
        respuesta"), cuando lo correcto es reconectar y seguir esperando: el
        módulo Qorvo sigue encendido y acumulando notificaciones, que llegan
        apenas vuelve la conexión. El tiempo de reconexión no consume el
        presupuesto de ``timeout_s`` del que llama. Tras
        ``_MAX_READ_RECONNECTS`` reconexiones seguidas (conexión
        persistentemente caída), recién ahí se propaga el error.
        """
        deadline = time.monotonic() + timeout_s
        poll_s = 0.05
        reconnects_left = _MAX_READ_RECONNECTS
        while True:
            if self._pending_error is not None:
                error = self._pending_error
                self._pending_error = None
                raise TransportError(f"{self.name}: {error}")
            remaining = deadline - time.monotonic()
            try:
                return source.get(timeout=min(poll_s, max(0.0, remaining)))
            except queue.Empty:
                pass
            if self._client is not None and not self._connected:
                if reconnects_left <= 0:
                    raise TransportError(f"{self.name}: conexión BLE perdida esperando {what}")
                reconnects_left -= 1
                reconnect_start = time.monotonic()
                self._ensure_connected()
                deadline += time.monotonic() - reconnect_start
                continue
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
        self._send_with_retry(text)
        self._drain_response()

    def power_off(self, hold_s: float | None = None) -> None:
        """``qorvo off``: apaga el módulo Qorvo (ver :meth:`power_on`)."""
        text = "off" if hold_s is None else f"off -t {hold_s:g}s"
        self._send_with_retry(text)
        self._drain_response()

    def enable_stream(self) -> None:
        """``qorvo stream on``: activa el streaming continuo de ranging por la
        característica dedicada (:data:`STREAM_DATA_CHAR_UUID`, ver comentario
        junto a esa constante).

        Palabra reservada del firmware puente, igual mecanismo que
        :meth:`power_on` (no pasa por ``write_line``): la confirmación
        (``"Qorvo streaming: ON"``) llega por el canal de comandos normal
        (NUS TX), sin marcador ``ok`` — se drena igual que la de ``power_on``.
        """
        self._send_with_retry("stream on")
        self._drain_response()

    def disable_stream(self) -> None:
        """``qorvo stream off`` (ver :meth:`enable_stream`).

        No es obligatorio llamarlo antes de desconectar — el streaming se
        apaga solo con la conexión BLE (ver doc del firmware puente) — pero
        es buena práctica hacerlo si se lo va a reactivar más adelante sobre
        la misma conexión.
        """
        self._send_with_retry("stream off")
        self._drain_response()

    def read_battery_level(self) -> int | None:
        """Nivel de batería del puente (0-100%), vía el Battery Service
        estándar (:data:`BATTERY_LEVEL_CHAR_UUID`) — no requiere el comando
        ``qorvo``, es una lectura GATT directa fuera del canal de comandos.

        Best-effort: es información complementaria, no crítica para el
        funcionamiento del transporte. Si el puente no expone el servicio o
        la lectura falla, se loguea y devuelve ``None`` en vez de propagar
        el error. Actualiza :attr:`battery_pct` con el mismo valor.
        """
        try:
            self._ensure_connected()
            data = self._run_coro(
                self._read_gatt_char(BATTERY_LEVEL_CHAR_UUID), timeout_s=self._write_timeout_s
            )
        except TransportError:
            logger.debug("%s: no se pudo leer el nivel de batería", self.name, exc_info=True)
            return None
        self.battery_pct = data[0] if data else None
        return self.battery_pct

    def read_bridge_firmware_version(self) -> str | None:
        """Versión de firmware del puente nRF52840 (no la del Qorvo — para
        esa ver ``DwmCliClient.stat()``), vía Device Information Service
        estándar (:data:`FIRMWARE_REV_CHAR_UUID`).

        Best-effort, mismo criterio que :meth:`read_battery_level`. Actualiza
        :attr:`bridge_firmware_version` con el mismo valor.
        """
        try:
            self._ensure_connected()
            data = self._run_coro(
                self._read_gatt_char(FIRMWARE_REV_CHAR_UUID), timeout_s=self._write_timeout_s
            )
        except TransportError:
            logger.debug(
                "%s: no se pudo leer la versión de firmware del puente", self.name, exc_info=True
            )
            return None
        self.bridge_firmware_version = data.decode("utf-8", errors="replace").strip() or None
        return self.bridge_firmware_version

    async def _read_gatt_char(self, char_uuid: str) -> bytes:
        if self._client is None:
            raise TransportError(f"{self.name}: no conectado")
        return bytes(await self._client.read_gatt_char(char_uuid))

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

    def _run_coro(self, coro: Coroutine[Any, Any, _T], *, timeout_s: float) -> _T:
        if self._loop is None:
            raise TransportError(f"{self.name}: transporte no abierto (llamar open() primero)")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout_s)
        except FutureTimeoutError as exc:
            # Cancelar la corutina huérfana: si se queda colgada (disconnect()
            # sobre una sesión muerta, p. ej.), no debe seguir para siempre.
            future.cancel()
            raise TransportError(f"{self.name}: timeout esperando una operación BLE") from exc
        except (BleakError, OSError) as exc:
            # OSError: el backend WinRT de bleak filtra errores crudos del SO
            # (p. ej. "[WinError -2147483629] Se cerró el objeto", RO_E_CLOSED)
            # cuando el cliente quedó cerrado por una caída GATT.
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
        self._run_coro(self._connect(), timeout_s=self._connect_timeout_s * _CONNECT_ATTEMPTS)
        # [Bug real, 2026-09-09, hardware real] El streaming (ver
        # enable_stream()) se apaga solo al desconectarse el BLE — es estado
        # de la conexión GATT, no algo persistente como el encendido físico
        # del Qorvo (power_on(), que es un GPIO y no hace falta reafirmar acá).
        # Antes de este fix, una reconexión automática (p. ej. el timeout de
        # inactividad de ~7-8s cayendo justo antes de arrancar el ranging)
        # dejaba el streaming apagado sin que nada lo notara: las
        # notificaciones de esa sesión no llegaban por ningún canal —
        # confirmado contra hardware real, GUI real: "0 notificaciones
        # recibidas en 100s" con el enlace BLE sano el resto del tiempo.
        #
        # [Bug real #2, 2026-09-09, hardware real] Reenviar "stream on" acá
        # NO debe usar enable_stream() (que hace _drain_response(), leyendo y
        # descartando de self._rx_queue hasta que haya silencio): si este
        # reconnect ocurre en medio de un read_line()/read_notification_line()
        # que ya está esperando la respuesta de un comando enviado ANTES de
        # la caída (p. ej. INITF), esa respuesta puede llegar recién ahora,
        # tras reconectar — y cae en la MISMA cola (_rx_queue) que el drain
        # está vaciando. Confirmado con hardware real: el eco completo de
        # INITF (más una ráfaga de SESSION_INFO_NTF de la sesión de ranging
        # que había seguido activa del lado del firmware durante el corte,
        # que temporalmente sale por el canal de comandos en vez del de
        # streaming hasta que el firmware procesa este mismo "stream on")
        # quedó registrado como "descartada tras encender/apagar" línea por
        # línea durante ~2.8s seguidos — el llamador original nunca vio la
        # respuesta real y terminó en un timeout de 10s sin ninguna pista.
        # Mandar el comando sin drenar es seguro: no toca self._rx_queue
        # (bypassea write_line(), como ya hacían power_on/power_off/
        # enable_stream para no interferir con el marcador de comando en
        # curso), así que cualquier respuesta pendiente sigue disponible para
        # quien la estaba esperando; el resto de la cola (la confirmación
        # "Qorvo streaming: ON" y cualquier ráfaga vieja) se descarta solo
        # con el próximo comando real, vía _reset_pending() en write_line().
        self._send_with_retry("stream on")

    async def _connect(self) -> None:
        last_error: Exception | None = None
        # [Mitigación 2026-09-09, motivada por hardware real] Cada reconexión
        # completa re-enumera todos los servicios/características del puente
        # por defecto — costo evitable, ya que solo se usa NUS. Se limita el
        # descubrimiento a ese único servicio y se le pide a Windows reusar su
        # caché de servicios ya conocido (``use_cached_services``), lo que
        # acelera la reconexión tras uno de los cortes espontáneos del puente
        # (ver docstring del módulo). Riesgo: si el catálogo GATT del puente
        # cambiara entre conexiones (p. ej. reflasheo de su firmware a mitad
        # de sesión), el caché quedaría desactualizado y esa conexión
        # fallaría. Por eso el caché es solo el camino rápido del primer
        # intento: cualquier fallo lo desactiva para el resto de los
        # intentos de esta llamada, priorizando terminar de conectar (más
        # lento, sin caché) por sobre la velocidad.
        use_cached_services = True
        for attempt in range(1, _CONNECT_ATTEMPTS + 1):
            # Nunca reusar el cliente anterior: tras una caída GATT su objeto
            # WinRT queda cerrado (RO_E_CLOSED) y hasta que Windows libera la
            # sesión puede fallar incluso la conexión nueva — por eso se
            # descarta y se reintenta con clientes nuevos.
            await self._dispose_client()
            client = self._client_factory(
                self._address,
                disconnected_callback=self._on_disconnect,
                services=[
                    NUS_SERVICE_UUID,
                    STREAM_SERVICE_UUID,
                    BATTERY_SERVICE_UUID,
                    DEVICE_INFO_SERVICE_UUID,
                ],
                winrt={"use_cached_services": use_cached_services},
            )
            try:
                await client.connect()
                await client.start_notify(NUS_TX_CHAR_UUID, self._on_notify)
                await client.start_notify(STREAM_DATA_CHAR_UUID, self._on_stream_notify)
            except (BleakError, OSError) as exc:
                last_error = exc
                logger.warning(
                    "%s: intento %d/%d de conexión falló (caché de servicios=%s): %s",
                    self.name,
                    attempt,
                    _CONNECT_ATTEMPTS,
                    use_cached_services,
                    exc,
                )
                use_cached_services = False
                await self._safe_disconnect(client)
                if attempt < _CONNECT_ATTEMPTS:
                    await asyncio.sleep(_CONNECT_RETRY_DELAY_S)
                continue
            self._client = client
            self._connected = True
            self._mtu_size = client.mtu_size
            logger.debug(
                "%s: conectado, MTU=%s, caché de servicios=%s",
                self.name,
                self._mtu_size,
                use_cached_services,
            )
            return
        raise TransportError(
            f"{self.name}: no se pudo conectar tras {_CONNECT_ATTEMPTS} intentos: {last_error}"
        ) from last_error

    async def _dispose_client(self) -> None:
        client = self._client
        self._client = None
        self._connected = False
        self._mtu_size = None
        if client is not None:
            await self._safe_disconnect(client)

    async def _safe_disconnect(self, client: _BleakClientLike) -> None:
        try:
            # [Bug real, 2026-09-08] Tras una caída GATT, disconnect() puede
            # quedar colgado en el backend WinRT: con tope de tiempo para no
            # bloquear la reconexión (el cliente se descarta de todos modos).
            await asyncio.wait_for(client.disconnect(), timeout=_DISCONNECT_TIMEOUT_S)
        except Exception:
            logger.debug("%s: fallo al descartar un cliente BLE viejo", self.name, exc_info=True)

    async def _disconnect(self) -> None:
        if self._client is None:
            return
        try:
            if self._connected:
                await self._client.stop_notify(NUS_TX_CHAR_UUID)
                await self._client.stop_notify(STREAM_DATA_CHAR_UUID)
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

    def _send_with_retry(self, text: str) -> None:
        """Escribe con reintentos: la escritura puede chocar con una caída
        GATT que recién se está procesando (el cliente quedó con el objeto
        WinRT cerrado) — se descarta el cliente, se reconecta con uno nuevo y
        recién agotados los intentos se propaga el error."""
        last_error: TransportError | None = None
        for attempt in range(1, _WRITE_ATTEMPTS + 1):
            self._ensure_connected()
            try:
                self._run_coro(self._send_raw(text), timeout_s=self._write_timeout_s)
                return
            except TransportError as exc:
                last_error = exc
                logger.warning(
                    "%s: escritura falló (intento %d/%d): %s",
                    self.name,
                    attempt,
                    _WRITE_ATTEMPTS,
                    exc,
                )
                if self._loop is not None:
                    try:
                        # Best-effort: el estado (cliente descartado) ya quedó
                        # actualizado; si el disconnect cuelga, no debe matar
                        # el reintento.
                        self._run_coro(
                            self._dispose_client(),
                            timeout_s=_DISCONNECT_TIMEOUT_S + 1.0,
                        )
                    except TransportError:
                        logger.debug(
                            "%s: descarte del cliente vencido; se reintenta igual",
                            self.name,
                            exc_info=True,
                        )
        raise TransportError(
            f"{self.name}: escritura falló tras {_WRITE_ATTEMPTS} intentos: {last_error}"
        ) from last_error

    def _on_disconnect(self, _client: object) -> None:
        # Corre en el hilo dedicado de bleak (self._thread), como todo lo que
        # toca self._client — seguro escribir acá el mismo atributo plano que
        # lee read_line() desde cualquier otro hilo.
        #
        # [Investigación 2026-09-09, hardware real] Este callback (y
        # _on_notify/_on_stream_notify de abajo) lo invoca DIRECTAMENTE la
        # capa nativa WinRT de bleak (pywinrt), no una señal Qt — a
        # diferencia de un slot Qt (que sí sobrevive una excepción sin
        # atrapar, confirmado con un test aislado), dejar escapar una
        # excepción de Python a través del límite C++/WinRT del callback no
        # tiene la misma garantía de seguridad y es una causa plausible de
        # un cierre nativo sin traza (investigado tras un cierre reportado
        # tres veces al conectar dos nodos por BLE, sin confirmación
        # definitiva de la causa raíz — ver docs/rama-hardware-ble.md §8).
        # Nunca dejar que una excepción cruce ese límite, la haya causado o
        # no en este caso puntual.
        try:
            self._connected = False
            logger.warning("%s: conexión BLE cerrada", self.name)
        except Exception:
            logger.critical("%s: excepción en _on_disconnect", self.name, exc_info=True)

    def _on_notify(self, _sender: object, data: bytearray) -> None:
        try:
            for line in self._assembler.feed(bytes(data)):
                if _PROMPT_RE.match(line):
                    logger.debug("%s: prompt de shell descartado: %r", self.name, line)
                    continue
                if line.startswith(_BRIDGE_TIMEOUT_MARKER):
                    logger.warning(
                        "%s: el puente reportó timeout hacia el Qorvo: %s", self.name, line
                    )
                    self._pending_error = line
                    continue
                logger.debug("RX %s: %s", self.name, line)
                self._rx_queue.put(line)
        except Exception:  # ver nota de _on_disconnect: nunca escapar al callback nativo
            logger.critical("%s: excepción en _on_notify", self.name, exc_info=True)

    def _on_stream_notify(self, _sender: object, data: bytearray) -> None:
        """Callback de la característica dedicada de streaming (ver
        :data:`STREAM_DATA_CHAR_UUID`) — cola separada de ``_on_notify``, sin
        el filtro de prompt de shell ni el marcador de timeout del puente:
        este canal es un passthrough del UART del Qorvo, no pasa por el shell
        de comandos (ver comentario junto a ``STREAM_SERVICE_UUID``).
        """
        try:
            for line in self._stream_assembler.feed(bytes(data)):
                logger.debug("STREAM %s: %s", self.name, line)
                self._stream_queue.put(line)
        except Exception:  # ver nota de _on_disconnect: nunca escapar al callback nativo
            logger.critical("%s: excepción en _on_stream_notify", self.name, exc_info=True)
