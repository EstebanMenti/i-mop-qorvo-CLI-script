"""Cliente del protocolo CLI del DWM3001C.

``DwmCliClient`` coordina el envío de comandos y la interpretación de las
respuestas sobre un ``Transport`` ya construido. No conoce Typer ni Rich
(reglas de arquitectura) ni abre puertos por su cuenta.

Comportamientos del firmware contemplados (guía + capturas reales):

- La placa hace **eco** del comando enviado; se descarta.
- Las respuestas de comandos terminan con una línea ``ok``; se usa como
  marcador de fin, con un período de silencio como respaldo.
- Los comandos de servicio e IDLE solo funcionan en modo NONE (guía §1.3);
  usar :meth:`DwmCliClient.ensure_mode_none` antes de invocarlos.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping

from dwm3001c_cli.core.errors import (
    CommandRejectedError,
    CommandTimeoutError,
    UnexpectedModeError,
)
from dwm3001c_cli.core.models import CalKey, ChipId, DeviceInfo, Measurement
from dwm3001c_cli.core.parsers import (
    is_ok,
    parse_calkey_line,
    parse_decaid,
    parse_listcal,
    parse_session_info,
    parse_stat,
)
from dwm3001c_cli.transport.serial_link import Transport

logger = logging.getLogger(__name__)

_VALID_APPS = {"LISTENER", "INITF", "RESPF", "NONE"}

# Vocabulario cerrado de comandos de la CLI (Developer Manual / guía §1.1).
# Usado por send_command para reconocer, cuando la primera línea de una
# respuesta no es el eco del comando propio, si en cambio es el eco de OTRO
# comando de este vocabulario: señal de que todo el bloque es la respuesta
# rezagada de un comando distinto (backlog), no la respuesta del que se acaba
# de enviar (ver nota "puente BLE" en send_command).
_CLI_COMMAND_WORDS = {
    "HELP",
    "STAT",
    "STOP",
    "THREAD",
    "RESTORE",
    "LCFG",
    "DIAG",
    "DECAID",
    "SAVE",
    "SETAPP",
    "GETOTP",
    "UART",
    "CALKEY",
    "LISTCAL",
    "INITF",
    "RESPF",
    "LISTENER",
}

# [Verificado 2026-08-06] Tras STOP, el firmware tarda un instante en volver a
# NONE: un STAT inmediato aún reporta la app anterior corriendo.
_STOP_SETTLE_S = 0.3
_PRFSETS = {"BPRF3", "BPRF4", "BPRF5", "BPRF6"}
_RRUS = {"SSTWR", "DSTWR", "SSTWRNDEF", "DSTWRNDEF"}
_VUPPER_RE = re.compile(r"^([0-9A-Fa-f]{2}:){7}[0-9A-Fa-f]{2}$")

# Orden canónico de emisión de opciones de INITF/RESPF (tabla 7.6 de la guía §2.4).
_OPTION_ORDER = (
    "chan",
    "prfset",
    "pcode",
    "slot",
    "block",
    "round",
    "rru",
    "id",
    "vupper",
    "multi",
    "hop",
    "addr",
    "paddr",
)


def _as_int(name: str, value: object, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"Opción {name!r}: se espera un entero entre {lo} y {hi}; llegó {value!r}")
    return value


def _format_app_options(params: Mapping[str, object]) -> list[str]:
    """Valida y formatea las opciones de INITF/RESPF como ``-OPCION=VALOR``.

    Los rangos provienen de la tabla 7.6 (guía §2.4). Cualquier opción fuera de
    la tabla, o valor fuera de rango, es ``ValueError`` — nunca se envía a la
    placa un comando dudoso.
    """
    unknown = set(params) - set(_OPTION_ORDER)
    if unknown:
        raise ValueError(f"Opciones desconocidas: {sorted(unknown)}; válidas: {_OPTION_ORDER}")

    parts: list[str] = []
    for name in _OPTION_ORDER:
        if name not in params:
            continue
        value = params[name]
        if name == "chan":
            channel = _as_int(name, value, 5, 9)
            if channel not in (5, 9):
                raise ValueError(f"Opción 'chan': solo canal 5 o 9; llegó {channel}")
            parts.append(f"-CHAN={channel}")
        elif name == "prfset":
            prfset = str(value).upper()
            if prfset not in _PRFSETS:
                raise ValueError(f"Opción 'prfset': válidos {sorted(_PRFSETS)}; llegó {value!r}")
            parts.append(f"-PRFSET={prfset}")
        elif name == "pcode":
            parts.append(f"-PCODE={_as_int(name, value, 9, 12)}")
        elif name == "slot":
            parts.append(f"-SLOT={_as_int(name, value, 2400, 65535)}")
        elif name == "block":
            parts.append(f"-BLOCK={_as_int(name, value, 1, 65535)}")
        elif name == "round":
            parts.append(f"-ROUND={_as_int(name, value, 1, 255)}")
        elif name == "rru":
            rru = str(value).upper()
            if rru not in _RRUS:
                raise ValueError(f"Opción 'rru': válidos {sorted(_RRUS)}; llegó {value!r}")
            parts.append(f"-RRU={rru}")
        elif name == "id":
            parts.append(f"-ID={_as_int(name, value, 1, 65535)}")
        elif name == "vupper":
            vupper = str(value)
            if _VUPPER_RE.match(vupper) is None:
                raise ValueError(
                    f"Opción 'vupper': formato XX:XX:XX:XX:XX:XX:XX:XX en hex; llegó {value!r}"
                )
            parts.append(f"-VUPPER={vupper}")
        elif name in ("multi", "hop"):
            # [Verificado 2026-08-06] HELP INITF del fw 1.1.0 confirma la
            # sintaxis: flags "-MULTI"/"-HOP" sin valor.
            if not isinstance(value, bool):
                raise ValueError(f"Opción {name!r}: se espera bool; llegó {value!r}")
            if value:
                parts.append(f"-{name.upper()}")
        elif name in ("addr", "paddr"):
            parts.append(f"-{name.upper()}={_as_int(name, value, 0, 65535)}")
    return parts


class DwmCliClient:
    """Cliente de la consola CLI de una placa DWM3001CDK.

    Args:
        transport: transporte ya construido (``SerialLink`` o fake); el cliente
            no lo abre ni lo cierra.
        command_timeout_s: tiempo máximo por defecto para esperar la respuesta
            de un comando.
        quiet_period_s: período de silencio por defecto que marca el fin de una
            respuesta sin ``ok``/``KO`` explícito (ver :meth:`send_command`).
            El default (0.3s) está calibrado para USB. [Verificado 2026-08-13,
            hardware real] Por BLE (rama ``hardware/ble-bridge-nrf52840``) se
            midieron gaps de ~590ms incluso entre fragmentos de una respuesta
            sana — con 0.3s, ``send_command`` corta la lectura a mitad de
            respuesta y el resto queda en la cola, contaminando el próximo
            comando. La capa ``app`` debe pasar un valor mayor (~1.5s) al
            construir un cliente sobre ``BleTransport``.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        command_timeout_s: float = 2.0,
        quiet_period_s: float = 0.3,
    ) -> None:
        self._transport = transport
        self._command_timeout_s = command_timeout_s
        self._quiet_period_s = quiet_period_s

    @property
    def name(self) -> str:
        return self._transport.name

    # ------------------------------------------------------------------ básicos

    def send_command(
        self,
        cmd: str,
        *,
        quiet_period_s: float | None = None,
        timeout_s: float | None = None,
    ) -> list[str]:
        """Envía un comando y recolecta su respuesta completa.

        Fin de respuesta: línea ``ok`` (marcador del firmware) o ``quiet_period_s``
        sin líneas nuevas tras haber recibido algo. El eco del comando se descarta.

        [Verificado 2026-08-10, bridge UART de J9] Por USB (J20) el eco llega
        siempre como línea propia. Por el bridge UART de J9, el eco puede llegar
        **pegado sin separador** al comienzo de la primera línea de respuesta
        (p. ej. ``"STAT\\rJS0109{...}"``, sin ``\\n`` entre medio, pero con un
        carácter de control justo después del texto del comando). Se detecta y
        recorta ese eco pegado sin tocar respuestas que casualmente empiezan
        con la misma palabra que el comando sin separador (p. ej. ``DIAG`` →
        ``"DIAG: 0"``, donde ``:`` sigue inmediatamente, sin espacio de por
        medio: eso es contenido real, no eco).

        [Verificado 2026-09-08, hardware real, puente BLE — UWB-Node-6/-8]
        Por el puente BLE (rama ``hardware/ble-bridge-nrf52840``), el sampler
        por polling (``calibration/poll_sampler.py``) reenvía un comando
        *anytime* (``THREAD``) repetidamente y las notificaciones
        ``SESSION_INFO_NTF`` que el Qorvo emite durante el ranging se
        acumulan en el puente y recién se entregan junto con la respuesta del
        próximo comando (nunca espontáneamente) — a veces decenas de golpe.
        Como las notificaciones BLE no tienen ACK ni retransmisión, es normal
        perder alguna; cuando la perdida es justo la que traía el eco+JSON+``ok``
        real de ``STAT``, o cuando la respuesta de un comando previo (p. ej.
        ``THREAD``) se demora y queda en la cola justo cuando se envía el
        siguiente, antes se devolvía ese contenido ajeno como si fuera la
        respuesta de ``STAT`` — se confirmaron tres variantes reales del mismo
        defecto: (a) la respuesta rezagada de ``THREAD`` completa, con su
        propio eco y su propio ``ok``; (b) decenas de notificaciones
        acumuladas seguidas del eco+``ok`` rezagado de un ``STOP`` anterior
        (el eco ajeno no estaba en la primera línea, sino al final de un
        backlog largo); y (c) puro backlog de notificaciones sin ningún eco
        de por medio, cortado por el período de silencio porque el ``ok``
        real nunca llegó (notificación perdida). Ahora, para el corte por
        ``ok``/``ko``: se revisa **cada** línea (no solo la primera) buscando
        el eco de **otro** comando del vocabulario cerrado de la CLI (guía
        §1.1) — si aparece, todo el bloque se trata como backlog rezagado y se
        descarta, sin exigir en general haber visto el eco propio (algunas
        respuestas reales, p. ej. ``CALKEY`` sobre una clave inexistente, no
        lo repiten). Para el corte por silencio (sin ``ok``/``ko``): sí se
        exige haber visto el eco propio — silencio con contenido pero sin eco
        propio es, con altísima probabilidad, backlog ajeno (caso (c)), nunca
        una respuesta real. En ambos casos se sigue esperando sin exceder
        ``timeout_s``, en vez de devolver contenido no atribuible al comando
        enviado.

        Raises:
            CommandTimeoutError: si no llegó ninguna línea dentro del timeout.
        """
        limit = timeout_s if timeout_s is not None else self._command_timeout_s
        quiet_period_s = quiet_period_s if quiet_period_s is not None else self._quiet_period_s
        cmd_upper = cmd.strip().upper()
        own_word = cmd_upper.split(maxsplit=1)[0] if cmd_upper else ""
        self._transport.write_line(cmd)
        deadline = time.monotonic() + limit
        lines: list[str] = []
        received_any = False
        own_echo_seen = False
        foreign_echo = False
        foreign_word = ""
        first_line_pending = True
        notification_backlog = False
        while True:
            if time.monotonic() >= deadline:
                raise CommandTimeoutError(self.name, cmd, limit)
            line = self._transport.read_line(quiet_period_s)
            if line is None:
                if received_any:
                    if own_echo_seen or not notification_backlog:
                        break
                    # [Bug real, 2026-09-08, hardware real, UWB-Node-6/-8]
                    # Silencio tras recibir *algo* que empieza como backlog de
                    # notificaciones SESSION_INFO_NTF, sin haber visto nunca
                    # el eco propio (ni un ok/ko, que ya habría cortado
                    # antes) — se confirmó contra hardware real que esto pasa
                    # cuando el puente BLE pierde justo la notificación que
                    # traía el eco+JSON+ok real de STAT (las notificaciones
                    # BLE no tienen ACK). Devolver ese backlog como si fuera
                    # la respuesta rompía el parseo; ahora se descarta y se
                    # sigue esperando, sin exceder timeout_s — un timeout
                    # franco en vez de un dato silenciosamente incorrecto. No
                    # se aplica a respuestas que no parecen notificaciones
                    # (p. ej. un rechazo de SAVE sin eco: ``"error: not
                    # allowed"``), que sí deben aceptarse tal cual.
                    logger.warning(
                        "%s: se descartó una respuesta sin eco propio de %r "
                        "(silencio, backlog de notificaciones): %r",
                        self.name,
                        cmd,
                        lines,
                    )
                    lines = []
                    received_any = False
                    foreign_echo = False
                    foreign_word = ""
                    first_line_pending = True
                    notification_backlog = False
                continue
            stripped = line.strip()
            if stripped == "":
                # Línea vacía (frecuente como residuo entre comandos): se
                # ignora sin contar para el timeout ni para el eco.
                continue
            received_any = True
            if first_line_pending:
                first_line_pending = False
                notification_backlog = stripped.startswith("SESSION_INFO_NTF")
            if not own_echo_seen and not foreign_echo:
                # Exige que la línea sea *solo* la palabra del comando (eco
                # verdadero de un comando sin parámetros, p. ej. "THREAD" a
                # secas): contenido real que meramente empieza con el nombre
                # de un comando (p. ej. el listado de "HELP": "STAT - report
                # status") siempre trae texto pegado a continuación y no debe
                # confundirse con un eco ajeno. Se revisa en **cada** línea,
                # no solo la primera: notificaciones SESSION_INFO_NTF
                # acumuladas pueden preceder por decenas de líneas al eco
                # ajeno que realmente delata el backlog (verificado 2026-09-08
                # contra hardware real: STAT devolvió ~40 notificaciones
                # seguidas del eco+ok rezagado de un STOP anterior).
                candidate = stripped.upper()
                if candidate != own_word and candidate in _CLI_COMMAND_WORDS:
                    foreign_echo = True
                    foreign_word = candidate
            if not own_echo_seen:
                if stripped.upper() == cmd_upper:
                    logger.debug("Eco descartado en %s: %s", self.name, line)
                    own_echo_seen = True
                    continue
                after_cmd = (
                    stripped[len(cmd_upper) :] if stripped.upper().startswith(cmd_upper) else None
                )
                # Eco pegado sin separador solo si justo después del texto del
                # comando hay un carácter de espacio/control (o nada): si sigue
                # un carácter de contenido (p.ej. ":") no es eco, es respuesta
                # real que empieza igual que el comando (guía: "DIAG" -> "DIAG: 0").
                if after_cmd is not None and (after_cmd == "" or after_cmd[0].isspace()):
                    remainder = after_cmd.lstrip()
                    logger.debug("Eco pegado a la respuesta en %s: %s", self.name, line)
                    own_echo_seen = True
                    if not remainder:
                        continue
                    stripped = remainder
                    line = remainder
            lines.append(line)
            # "ok" y "KO" son los marcadores de fin de respuesta del firmware
            # (éxito y error respectivamente; el KO se observó en fw 1.1.0).
            if stripped.lower() in ("ok", "ko"):
                if own_echo_seen or not foreign_echo:
                    break
                # El bloque contiene, en alguna línea, el eco de OTRO comando
                # de la CLI (ver nota arriba): es la respuesta rezagada de ese
                # comando — se descarta y se sigue esperando la respuesta real,
                # sin exceder el timeout.
                logger.warning(
                    "%s: se descartó una respuesta ajena a %r (eco de %r, backlog rezagado): %r",
                    self.name,
                    cmd,
                    foreign_word,
                    lines,
                )
                lines = []
                received_any = False
                foreign_echo = False
                foreign_word = ""
                first_line_pending = True
                notification_backlog = False
        return lines

    # ----------------------------------------------------------- estado y modo

    def stat(self) -> DeviceInfo:
        """``STAT``: información y modo actual del dispositivo."""
        return parse_stat(self.send_command("STAT"))

    def stop(self) -> None:
        """``STOP``: detiene la aplicación en curso; tolera silencio (sin app)."""
        try:
            self.send_command("STOP")
        except CommandTimeoutError:
            logger.debug("STOP sin respuesta en %s (¿ya estaba en NONE?)", self.name)

    def ensure_mode_none(self) -> None:
        """Lleva el dispositivo a modo NONE, requisito de los comandos de servicio.

        Envía ``STOP`` y verifica con ``STAT``; reintenta una vez (guía §1.3).

        Raises:
            UnexpectedModeError: si tras dos intentos el modo no es NONE.
        """
        info: DeviceInfo | None = None
        for attempt in range(2):
            self.stop()
            time.sleep(_STOP_SETTLE_S)
            info = self.stat()
            if info.mode == "NONE":
                return
            log = logger.warning if attempt else logger.debug
            log("El dispositivo %s sigue en modo %s tras STOP", self.name, info.mode)
        mode = info.mode if info is not None else "?"
        raise UnexpectedModeError(
            f"{self.name}: el dispositivo reporta modo {mode!r} y se requiere NONE"
        )

    # ------------------------------------------------------------- calibración

    def listcal(self) -> dict[str, CalKey]:
        """``LISTCAL``: todas las claves de calibración (requiere modo NONE)."""
        return parse_listcal(self.send_command("LISTCAL", timeout_s=5.0))

    def calkey_read(self, key: str) -> CalKey:
        """``CALKEY <key>``: lee una clave de calibración (requiere modo NONE).

        [Verificado 2026-08-06] En fw 1.1.0 la forma de lectura de ``CALKEY``
        está rota: responde ``KO`` para cualquier clave, incluso las listadas
        por ``LISTCAL``. Como respaldo, la clave se lee filtrando ``LISTCAL``.
        """
        lines = self.send_command(f"CALKEY {key}")
        for line in lines:
            try:
                cal_key = parse_calkey_line(line)
            except ValueError:
                continue
            if cal_key.name == key:
                return cal_key
        logger.debug("CALKEY %s sin respuesta directa en %s; leyendo vía LISTCAL", key, self.name)
        keys = self.listcal()
        if key in keys:
            return keys[key]
        raise CommandRejectedError(
            f"{self.name}: la clave {key} no existe "
            f"(CALKEY respondió {lines!r} y no figura en LISTCAL)"
        )

    def calkey_write(self, key: str, value: int) -> CalKey:
        """``CALKEY <key> <value>``: escribe y **verifica releyendo** (plan §4.3).

        El valor se envía en decimal. [Verificado 2026-08-06] El firmware
        interpreta la entrada en decimal: escribir ``10`` produce ``0x0a``.

        Raises:
            CommandRejectedError: si la relectura no coincide con lo escrito.
        """
        if value < 0:
            raise ValueError(f"Valor de clave negativo no soportado: {value}")
        self.send_command(f"CALKEY {key} {value}")
        written = self.calkey_read(key)
        if written.value != value:
            raise CommandRejectedError(
                f"{self.name}: se escribió {key}={value} pero la relectura "
                f"devolvió {written.value} — formato de entrada sospechoso"
            )
        return written

    # ---------------------------------------------------------------- servicio

    def save(self) -> None:
        """``SAVE``: persiste la configuración en NVM (modo NONE; no durante ranging)."""
        lines = self.send_command("SAVE")
        if not is_ok(lines):
            raise CommandRejectedError(f"{self.name}: SAVE no confirmó ok; respuesta: {lines!r}")

    def diag(self, enable: bool) -> None:
        """``DIAG 0|1``: habilita/deshabilita el modo diagnóstico (RSSI en ranging)."""
        lines = self.send_command(f"DIAG {int(enable)}")
        if not is_ok(lines):
            raise CommandRejectedError(f"{self.name}: DIAG no confirmó ok; respuesta: {lines!r}")

    def setapp(self, app: str) -> None:
        """``SETAPP``: define la aplicación por defecto tras reboot (requiere SAVE)."""
        normalized = app.upper()
        if normalized not in _VALID_APPS:
            raise ValueError(f"App inválida {app!r}; válidas: {sorted(_VALID_APPS)}")
        lines = self.send_command(f"SETAPP {normalized}")
        if not is_ok(lines):
            raise CommandRejectedError(f"{self.name}: SETAPP no confirmó ok; respuesta: {lines!r}")

    def decaid(self) -> ChipId:
        """``DECAID``: identificación del chip UWB."""
        return parse_decaid(self.send_command("DECAID"))

    def getotp(self) -> list[str]:
        """``GETOTP``: volcado crudo de la memoria OTP."""
        return self.send_command("GETOTP", timeout_s=5.0)

    def thread(self) -> list[str]:
        """``THREAD``: información cruda de hilos y memoria."""
        return self.send_command("THREAD")

    def help_cmd(self, cmd: str | None = None) -> list[str]:
        """``HELP`` o ``HELP <CMD>``: ayuda cruda del firmware."""
        return self.send_command("HELP" if cmd is None else f"HELP {cmd.upper()}")

    def uart_status(self) -> list[str]:
        """``UART`` (solo consulta). La escritura ``UART 0/1`` no se implementa:
        deshabilitar el USB por error dejaría la consola inaccesible."""
        return self.send_command("UART")

    def enable_uart_output(self) -> None:
        """``UART 1``: conmuta la consola del firmware a los pines UART físicos.

        [Verificado, ``docs/referencia-comandos-fw110.md`` §3.1] ``UART <DEC>``
        **no agrega** una segunda salida: conmuta cuál de las dos interfaces
        (USB CDC nativo o pines UART) recibe *todas* las respuestas del
        firmware. Tras ``UART 1`` + ``SAVE``, la placa **deja de responder por
        su USB nativo** y el cambio persiste a través de reinicios — no es una
        habilitación aditiva y segura, es una conmutación con pérdida real de
        acceso por el puerto usado para invocar este mismo método. Revertirlo
        (``UART 0`` + ``SAVE``) requiere acceso físico a los pines UART; el
        efecto de ``RESTORE`` sobre esta configuración **no está verificado**
        en este proyecto (``RESTORE`` nunca se ejecutó contra hardware real,
        solo se capturó su ayuda).

        Deliberadamente no acepta parámetro: nunca debe poder enviarse
        ``UART 0`` desde código automatizado (CLAUDE.md §2.2). Pero notar que
        la variante que sí expone — ``UART 1`` — igualmente dejará inaccesible
        el USB nativo de la placa sobre la que se ejecuta; quien la invoque
        debe estar seguro de que esa placa se va a seguir usando exclusivamente
        por los pines UART (p. ej. cableada a un puente Bluetooth externo,
        rama ``hardware/ble-bridge-nrf52840``) y no por su USB nativo.
        """
        lines = self.send_command("UART 1")
        if not is_ok(lines):
            raise CommandRejectedError(f"{self.name}: UART 1 no confirmó ok; respuesta: {lines!r}")

    def lcfg(self) -> list[str]:
        """``LCFG``: configuración cruda de la aplicación LISTENER (guía §2.2)."""
        return self.send_command("LCFG")

    def restore(self) -> None:
        """``RESTORE`` — **DESTRUCTIVO**: pisa configuración y calibración L1 con
        los valores por defecto y los escribe en NVM automáticamente (guía §2.2).

        Ninguna otra función del paquete debe llamarla; solo la capa de
        aplicación, con confirmación explícita del usuario (CLAUDE.md §6).
        """
        lines = self.send_command("RESTORE", timeout_s=5.0)
        if not is_ok(lines):
            raise CommandRejectedError(f"{self.name}: RESTORE no confirmó ok; respuesta: {lines!r}")

    # ------------------------------------------------------------ aplicaciones

    def start_initf(self, **params: object) -> None:
        """``INITF``: inicia el rol INITIATOR de una sesión FiRa TWR.

        Acepta solo las opciones de la tabla 7.6 (``chan``, ``prfset``, ``pcode``,
        ``slot``, ``block``, ``round``, ``rru``, ``id``, ``vupper``, ``multi``,
        ``hop``, ``addr``, ``paddr``), validadas antes de enviar. Recordar que
        pasar cualquier parámetro resetea los demás a default (guía §2.4).
        """
        self._start_app("INITF", params)

    def start_respf(self, **params: object) -> None:
        """``RESPF``: inicia el rol RESPONDER de una sesión FiRa TWR (ver start_initf)."""
        self._start_app("RESPF", params)

    def start_listener(self) -> None:
        """``LISTENER``: inicia el modo sniffer (sin parámetros por ahora)."""
        self._start_app("LISTENER", {})

    def _start_app(self, app: str, params: Mapping[str, object]) -> None:
        command = " ".join([app, *_format_app_options(params)])
        lines = self.send_command(command)
        if not is_ok(lines):
            # Algunas apps pueden arrancar sin emitir ok inmediato; no se aborta.
            logger.warning("%s en %s no confirmó ok; respuesta: %r", app, self.name, lines)

    def read_notifications(
        self,
        *,
        duration_s: float | None = None,
        max_count: int | None = None,
        on_measurement: Callable[[Measurement], None] | None = None,
    ) -> list[Measurement]:
        """Lee notificaciones ``SESSION_INFO_NTF`` durante una sesión de ranging.

        Corta al alcanzar ``max_count`` mediciones o al vencer ``duration_s``
        (al menos uno es obligatorio). Sin ``duration_s``, retorna en el primer
        silencio del puerto. Las líneas que no son notificaciones se ignoran;
        las notificaciones mal formadas se loguean y se descartan.

        [Verificado 2026-08-06] En fw 1.1.0 cada ``SESSION_INFO_NTF`` llega
        partida en dos líneas (la continuación arranca con un ``\\r`` residual);
        se reensambla acumulando líneas hasta balancear las llaves ``{}``.
        """
        if duration_s is None and max_count is None:
            raise ValueError("Indicar duration_s y/o max_count")
        deadline = None if duration_s is None else time.monotonic() + duration_s
        measurements: list[Measurement] = []
        fragment: list[str] = []
        while True:
            if max_count is not None and len(measurements) >= max_count:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            line = self._transport.read_line(0.5)
            if line is None:
                if deadline is None:
                    break
                continue
            stripped = line.strip()
            if stripped.startswith("SESSION_INFO_NTF"):
                fragment = [stripped]
            elif fragment:
                fragment.append(stripped)
            else:
                continue
            joined = " ".join(fragment)
            if joined.count("{") > joined.count("}"):
                if len(fragment) > 8:
                    logger.warning(
                        "Notificación inconclusa descartada en %s: %r", self.name, joined
                    )
                    fragment = []
                continue
            fragment = []
            try:
                measurement = parse_session_info(joined)
            except ValueError:
                logger.warning("Notificación no parseable en %s: %r", self.name, joined)
                continue
            measurements.append(measurement)
            if on_measurement is not None:
                on_measurement(measurement)
        return measurements
