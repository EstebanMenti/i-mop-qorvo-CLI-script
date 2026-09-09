# Rama `hardware/ble-bridge-nrf52840` — RESPONDER por puente Bluetooth + GUI

> **Propósito:** documentar el alcance, el hardware necesario y el flujo de
> trabajo de esta rama de larga vida, que **nunca se mergea a `main`**.
> **Alcance:** agrega un modo de operación donde el board RESPONDER se
> alcanza por un puente Bluetooth Low Energy (nRF52840) en vez de por USB, y
> una interfaz gráfica de escritorio (PySide6) para operar la herramienta de
> forma manual y automática.

---

## 1. Por qué esta rama existe y por qué no se mergea a `main`

`main` cubre el banco de pruebas estándar: dos placas DWM3001CDK conectadas
por USB a la misma PC. Esta rama agrega un banco alternativo donde el
RESPONDER es un Qorvo DWM3001C cableado por UART a una placa nRF52840 que lo
expone por Bluetooth (firmware del repo hermano
`I-mop-nrf52840-fw`, ya implementado y validado en hardware real). Ese
hardware específico (nRF52840 + cableado UART a un segundo Qorvo) no siempre
está disponible, y el código que depende de él (transporte BLE, GUI de
escritorio) no debe convertirse en un requisito para quien solo usa el flujo
USB-USB de `main`. Por eso queda aislado en una rama `hardware/` de larga
vida en vez de mergearse — ver la excepción documentada en
[`CLAUDE.md` §5.1](../CLAUDE.md).

## 2. Qué agrega esta rama sobre `main`

| Capacidad | Comando / módulo |
|---|---|
| Habilitar la salida UART física del Qorvo del lado BLE (paso único, por USB) | `dwm ble-provision --port COMx` |
| Descubrir el puente nRF52840 por Bluetooth | `dwm ble-scan` |
| Usar el puente BLE como RESPONDER en validación | `dwm validate --second-ble-address <addr>` |
| Usar el puente BLE como RESPONDER en calibración | `dwm calibrate --responder-ble-address <addr>` |
| GUI de escritorio (conexión, terminal manual, validación y calibración con gráfico en vivo) | `dwm-gui` |

El rol Bluetooth es **siempre RESPONDER**, nunca INITIATOR, en el flujo base
de esta rama (`dwm validate`/`dwm calibrate` con `--second-ble-address`/
`--responder-ble-address`): la calibración y la validación solo leen
notificaciones `SESSION_INFO_NTF` del lado INITIATOR (USB), nunca del lado
RESPONDER.

> **[Ampliado en `feature/gui-calibracion-ble-ambos-nodos`]** La GUI
> (`dwm-gui`, pestaña "Calibración BLE") sí permite **las dos placas por
> Bluetooth simultáneamente** (ninguna por USB) — ver
> [gui-calibracion-ble.md](gui-calibracion-ble.md). Esa extensión fue la que
> reveló el bug del canal de comandos documentado en §7.3 (con una sola
> placa por BLE, sesiones de calibración típicas nunca llegaban a los ~8 s
> que hacía falta para que el problema apareciera).

## 3. Hardware necesario

- 1 Qorvo DWM3001CDK por USB, como INITIATOR (igual que en `main`).
- 1 Qorvo DWM3001C cableado por UART a una placa nRF52840 (TX nRF→P0.08,
  RX Qorvo→P0.06 según el firmware puente), con el firmware de
  `I-mop-nrf52840-fw` flasheado, como RESPONDER.
- Ese mismo Qorvo, conectado por USB **una única vez**, para el paso de
  `dwm ble-provision` (ver §5).

## 4. Instalación

Esta rama agrega dependencias opcionales que `main` no tiene, para no
forzarlas a quien solo usa el flujo USB-USB:

```powershell
# Transporte Bluetooth (bleak)
pip install -e .[ble]

# GUI de escritorio (PySide6 + pyqtgraph)
pip install -e .[gui]

# Todo junto, incluyendo dependencias de desarrollo
pip install -e .[dev,ble,gui]
```

## 5. Puesta en marcha del RESPONDER Bluetooth

1. **Provisioning único** (mientras el Qorvo del lado BLE está conectado por
   USB): `dwm ble-provision --port COMx`. Habilita `UART 1` y hace `SAVE` —
   sin este paso el puente nRF52840 no puede hablarle por UART (de fábrica el
   Qorvo solo responde por USB).
2. Desconectar el Qorvo del USB, conectarlo por UART al nRF52840 (según el
   cableado documentado en `I-mop-nrf52840-fw`), y encender el conjunto.
3. `dwm ble-scan` para confirmar que el puente anuncia `"UWB Node"` por BLE.
4. Usar `--second-ble-address`/`--responder-ble-address` en `validate`/
   `calibrate`, o la GUI (`dwm-gui`).

## 6. Sincronización con `main`

Rama de larga vida: se actualiza trayendo cambios de `main` por **merge**
(nunca rebase, para no romper ramas cortas de fase abiertas contra esta
rama), pero nunca se mergea de vuelta a `main`.

```bash
git checkout hardware/ble-bridge-nrf52840
git fetch origin
git merge origin/main
git push origin hardware/ble-bridge-nrf52840
```

Cadencia: antes de empezar cada fase nueva (ver §7), y cada vez que `main`
tenga cambios en `core/`, `calibration/` o `validation/` (las capas que esta
rama reusa sin modificar su contrato). Conflictos esperables: mínimos, ya que
esta rama solo agrega opciones nuevas a comandos existentes y métodos nuevos,
no reescribe lógica compartida.

Las ramas cortas de trabajo dentro de esta rama siguen la convención habitual
(`feature/f7-ble-provision`, `feature/f8-ble-transport`, `feature/f9-gui`,
`feature/f10-hardware-verification`), pero sus PR apuntan **contra
`hardware/ble-bridge-nrf52840`**, no contra `main`.

## 7. Fases de implementación

| Fase | Contenido | Estado |
|---|---|---|
| F7 | `dwm ble-provision`: habilita `UART 1` + `SAVE` en el Qorvo del lado BLE (por USB) | implementado; **no aplicable a la placa RESPONDER actual** (ver nota §7.1) |
| F8 | `transport/ble_link.py` (`BleTransport` sobre Nordic UART Service vía `bleak`), `transport/ble_discovery.py`, wiring en `app/cli.py` | **implementado y verificado contra hardware real** (2026-08-13): `ensure_mode_none`, `STAT` y `LISTCAL` completo (259 claves, la respuesta más grande) llegaron íntegros por BLE — ver §7.2 |
| F9 | GUI de escritorio PySide6 (`src/dwm3001c_cli/gui/`): conexión, terminal manual, validación y calibración con gráfico en vivo | **Verificado contra hardware real (2026-08-25): USB confiable; el connect BLE del RESPONDER es intermitente por un bug de fondo en la capa nativa WinRT — ver §8, fila "Crash intermitente al conectar BLE"** |
| F10 | Verificación end-to-end contra hardware real (`validate`/`calibrate` completos, sesión TWR real, direccionamiento ADDR/PADDR) | **completo y verificado** (2026-08-13): sesión TWR real 50/50 SUCCESS, `dwm validate` 17/17 PASS con BLE primaria, recalibración real convergida (16376→16200), y las 4 combinaciones de direccionamiento ADDR/PADDR verificadas — ver [resultados-verificacion-ble.md](resultados-verificacion-ble.md) |

F7 va primero porque no depende de BLE (usa `SerialLink` normal) y es
precondición física de todo lo demás.

### 7.1 Corrección importante sobre `UART <DEC>` — no es aditivo, es exclusivo

[Verificado, `docs/referencia-comandos-fw110.md` §3.1] El comando `UART <DEC>`
**no agrega** una segunda salida: **conmuta** cuál interfaz (USB CDC nativo o
pines UART) recibe toda la consola del firmware. Tras `UART 1` + `SAVE`, la
placa **deja de responder por su USB nativo**, de forma persistente a través
de reinicios; revertirlo requiere acceso físico a los pines UART, y el efecto
de `RESTORE` sobre esta configuración **no está verificado** (nunca se
ejecutó contra hardware real en este proyecto). El docstring original de
`enable_uart_output()` y el mensaje de confirmación de `dwm ble-provision`
subestimaban esto (fue corregido — ver `core/client.py` y `app/cli.py`).

**Consecuencia práctica:** el paso de provisioning tiene que hacerse *antes*
de que la placa quede físicamente inaccesible por USB (p. ej. antes de
cablearla de forma permanente al nRF52840), porque después no hay forma de
revertir el problema sin acceso físico a los pines UART.

**Estado real de la placa RESPONDER de este banco (2026-08-13):** ya estaba
provisionada de antes — probado con una app de terminal BLE genérica en un
celular (no con esta herramienta): `qorvo stat` devolvió el JSON completo de
`STAT` (`"Current App":"NONE"`, `"Build":"Aug 10 2026 16:03:38"` — el build
con el fix de transporte UART documentado en
`docs/referencia-comandos-fw110.md` §0.1) en ~620 ms extremo a extremo. No
hace falta correr `dwm ble-provision` sobre esta placa puntual — y no se
podría, porque su USB es físicamente inaccesible ahora. `ble-provision` sigue
siendo necesario para **placas nuevas**, antes de cablearlas al nRF52840.

**Detalle de protocolo nuevo, a incorporar en el diseño de F8:** en esa misma
captura aparece la línea literal `bt_nus:~$` (el prompt del shell de Zephyr)
al final de cada respuesta, antes del siguiente comando — no mencionado en la
especificación del firmware puente citada originalmente. `BleTransport`
(F8) va a tener que filtrarla, igual que hoy se descarta el eco por USB en
`DwmCliClient.send_command`.

### 7.2 Dos bugs reales encontrados y corregidos durante la verificación de F8

Implementado `BleTransport`/`ble_discovery.py` reusando `DwmCliClient` sin
cambios (confirmado: es transporte-agnóstico como estaba previsto). Un smoke
test con la implementación de producción (no el prototipo de §7.1) contra la
placa RESPONDER real reveló dos bugs reales, diagnosticados con timestamps
relativos precisos:

1. **`power_on()`/`power_off()` no consumían su propia respuesta.** El
   firmware puente responde a `qorvo on` con `"Qorvo status changed to: ON"`
   pero **sin marcador `ok`/`KO`** (a diferencia de los comandos CLI reales) —
   nada en `send_command` esperaba eso, porque `power_on()` nunca llamaba a
   `read_line()`. Esa línea quedaba sin consumir en la cola interna de
   `BleTransport`, y el **siguiente comando real** (`STOP`, enviado por
   `ensure_mode_none()`) la heredaba como si fuera su propia respuesta.
   Confirmado con timestamps: `qorvo on` respondió en ~90ms, pero como nadie
   la leyó, quedó ahí hasta que `STOP` la consumió por error 3 segundos
   después. Corregido: `power_on()`/`power_off()` ahora drenan su propia
   respuesta con `_drain_response()` antes de devolver el control
   (`transport/ble_link.py`).
2. **`quiet_period_s=0.3` (default de `DwmCliClient`, calibrado para USB) es
   insuficiente para BLE.** Medido con hardware real: hasta **~590ms** de gap
   entre el eco de un comando y el resto de su respuesta — muy por encima de
   los 300ms de silencio que `send_command` tolera antes de dar la respuesta
   por terminada. Esto cortaba la lectura a mitad de respuesta, dejando el
   resto en la cola para contaminar el próximo comando (el mismo síntoma que
   el bug 1, por una causa distinta). Corregido: `DwmCliClient` ahora acepta
   `quiet_period_s` en el constructor (antes solo por llamada a
   `send_command`); `app/cli.py` pasa `quiet_period_s=1.5` para los clientes
   BLE (`_BLE_QUIET_PERIOD_S`).

**Sin ambos fixes, cualquier secuencia de comandos sobre BLE con más de un
paso (exactamente lo que hacen `ensure_mode_none`, `validate` y `calibrate`)
fallaba de forma intermitente y confusa** (un `ValueError` de parseo en un
comando que nada tenía que ver con el que realmente falló). Verificado tras
el fix: `ensure_mode_none()` + `STAT` + `LISTCAL` (259 claves) corridos en
secuencia contra la placa real, sin errores, con la implementación de
producción (`transport/ble_link.py`, no el prototipo).

### 7.3 Bug real de fondo: el canal de comandos suspendía el UART tras 8s de ranging sostenido — corregido con un canal BLE dedicado

[Confirmado contra hardware real y contra el código fuente del firmware
puente, 2026-09-09] Con ambas placas por BLE (ver nota de §2), sesiones de
calibración de 100 muestras entregaban siempre **~40 muestras SUCCESS en una
única ráfaga inicial y silencio total después**, sin ninguna desconexión BLE
de por medio. Investigado a fondo, en tres etapas:

1. **Síntoma medido**: con `BLOCK=200` ms, el corte ocurría siempre en
   40-44 muestras — `8000 / 200 = 40`, coincidiendo con un timeout
   documentado en otro contexto (ver §8, fila del marcador de timeout del
   puente).
2. **Causa raíz confirmada en `I-mop-nrf52840-fw/src/qorvo_bridge.c`**: el
   comando `qorvo <cmd>` (usado también para arrancar `RESPF`/`INITF`) es
   petición/respuesta: acumula todo lo que llega por UART hasta detectar
   400 ms de silencio (`QORVO_SILENCE_TIMEOUT_MS`) o vencer un límite duro
   de 8000 ms (`QORVO_TOTAL_TIMEOUT_MS`). Con `SESSION_INFO_NTF` llegando
   cada `BLOCK` ms sin pausa durante una sesión de ranging activa, el
   silencio **nunca se cumple** — la ventana corre siempre hasta los 8000 ms,
   vuelca todo lo acumulado como si fuera la respuesta de ese comando, y
   **acto seguido deshabilita la IRQ de recepción UART y suspende el
   periférico incondicionalmente** (`uart_irq_rx_disable()` +
   `pm_device_runtime_put()`). El Qorvo sigue midiendo y transmitiendo, pero
   el puente ya no escucha — sin que haya ninguna desconexión BLE de por
   medio. El UART solo se reactiva dentro de la invocación de un comando
   `qorvo` nuevo, que entra en el mismo ciclo. Esta limitación de diseño
   (petición/respuesta, no streaming) ya estaba documentada por el propio
   firmware puente (`doc/00_BLE_Protocol_Specification.md`, sección
   "Limitación conocida de este diseño").
3. **Corrección en el firmware puente**: nuevo servicio GATT dedicado,
   solo-Notify, para streaming continuo — `STREAM_SERVICE_UUID`
   (`019dad38-2b03-4df9-ac87-70ce530540fb`) /
   `STREAM_DATA_CHAR_UUID` (`36a9a2d9-a035-440f-8e59-ff0a72b2ba51`),
   documentado en `I-mop-nrf52840-fw/doc/00_BLE_Protocol_Specification.md`
   §5.4/§7.7. Se activa con el comando reservado `qorvo stream on` (enviado
   por el canal de comandos normal, igual mecanismo que `qorvo on`/`qorvo
   off`) y dejar el UART abierto de forma indefinida, reenviando todo por
   la característica dedicada — el canal de comandos sigue funcionando en
   paralelo sin cambios. Se apaga con `qorvo stream off`, o solo, al
   desconectarse el BLE.

**Cambios del lado cliente** (`transport/ble_link.py`):

- `BleTransport` se suscribe a la característica dedicada al conectar y
  llama a `enable_stream()` (equivalente a `power_on()`, mismo patrón)
  después de encender el Qorvo. `DwmCliClient.read_notifications()` lee de
  ese canal (`read_notification_line()`), separado del canal de comandos
  (`read_line()`) — nunca se mezclan, para que un `STAT` no compita por
  datos con una sesión de ranging en curso.
- **[Bug real, corregido]** El streaming es estado de la sesión GATT, no
  algo persistente como el encendido físico del Qorvo — se apaga solo al
  caerse la conexión BLE. La primera versión de este fix solo lo activaba
  una vez, en `open()`; una reconexión automática (el timeout de
  inactividad de ~7-8 s, fila de abajo, cayendo antes de arrancar el
  ranging) dejaba el streaming apagado sin que nada lo notara —
  confirmado contra hardware real, GUI real: "0 notificaciones recibidas
  en 100 s" con el enlace BLE sano el resto del tiempo. Corregido:
  `_ensure_connected()` (la reconexión automática de `read_line`/
  `write_line`) reactiva el streaming después de **cualquier** reconexión,
  no solo la primera vez.
- Se probó (y se descartó) un keepalive `STAT` periódico al RESPONDER
  durante el muestreo, portado del repo hermano `i-mop-tools-measure` —
  necesario cuando las notificaciones viajaban por el canal de comandos,
  contraproducente con el streaming dedicado activo (reabre la ventana de
  8 s del canal de comandos para la respuesta de ese `STAT` específico).

**Verificado contra hardware real** (UWB-Node-6/UWB-Node-8, firmware puente
actualizado): sesión de 100 muestras, 100/100 SUCCESS, flujo continuo
(~200 ms entre muestras, sin ráfagas ni huecos), ~20 s de ranging efectivo —
el comportamiento nominal esperado. Repetido varias veces sin regresión,
incluida una corrida donde la reconexión automática ocurrió en vivo antes
de arrancar el ranging y el streaming se reactivó correctamente.

### 7.4 Batería y versión de firmware del puente en la GUI

[Agregado 2026-09-09] `BleTransport.read_battery_level()`/
`read_bridge_firmware_version()` leen los servicios GATT estándar que ya
expone el puente — Battery Service (`0x180F`, característica `0x2A19`) y
Device Information Service (`0x180A`, característica `0x2A26`), ver
`I-mop-nrf52840-fw/doc/00_BLE_Protocol_Specification.md` §5.1/§5.2 — sin
pasar por el canal de comandos `qorvo <cmd>`. Best-effort: una falla no
propaga excepción, devuelve ``None``. Ambas pestañas muestran el mismo texto
(batería %, versión de firmware del puente — no la del Qorvo, para esa ver
`DwmCliClient.stat()`).

**[Mitigación 2026-09-09, hardware real]** La primera versión leía
batería/firmware automáticamente, justo después de `open()`, para cada nodo
apenas se conectaba. Se confirmó contra hardware real (cuatro reproducciones,
ver la fila de crash en §8) que esa lectura automática, ocurriendo en el
mismo momento en que el *otro* nodo podía estar terminando de conectar (dos
hilos de `BleTransport` distintos, cada uno con su propia actividad WinRT
nativa), aumentaba significativamente la frecuencia del crash nativo
documentado en §8 (`STATUS_STACK_BUFFER_OVERRUN`/`0xC0000409`, confirmado con
`ProcDump`). Mitigación aplicada (no elimina la causa raíz, reduce la
superposición temporal que la dispara):

- **`ConnectionView`** (pestaña "Conexión"): ya no lee nada automáticamente.
  Al conectar por BLE aparece un botón "Ver info (batería/firmware)" por rol
  (INITIATOR/RESPONDER); el usuario lo dispara a mano, un nodo a la vez, con
  la conexión ya estable. Corre en un `CallableWorker` (hilo aparte, no
  bloquea la UI) — ver `_fetch_ble_device_status()` en `connection_view.py`.
- **`BlePairCalibrationWorker`** (pestaña "Calibración BLE"): sigue siendo
  automático (no hay botón por nodo en esta pestaña), pero las dos lecturas
  se movieron a después de que **ambas** conexiones (`initiator_transport` y
  `dut_transport`) están abiertas y estables, y se hacen una después de la
  otra — nunca mientras el otro transporte todavía está conectando.

**Verificado contra hardware real** (UWB-Node-8, UWB-Node-10): lectura
correcta en ambos nodos (batería 85%/99%, firmware `0.3.0+0` en los dos).

## 8. Riesgos e incertidumbres a verificar contra hardware real

No inventar comportamiento no documentado — esta tabla se actualiza con el
resultado real de F10.

| Riesgo | Por qué importa | Resultado |
|---|---|---|
| MTU efectivo con `bleak`/WinRT en Windows (no solo con una app de celular) | Si es insuficiente, las respuestas (o la escritura de `RESPF`/`INITF`) se truncan | **Confirmado** (2026-08-13): MTU negociado **247**; `STAT` **y `LISTCAL` completo (259 líneas, la respuesta más grande)** llegaron enteros, con la implementación de producción (`transport/ble_link.py`). Falta todavía confirmar la escritura saliente de un `RESPF`/`INITF` completo (~130+ caracteres) |
| Latencia real del puente | Define si los timeouts del cliente Python alcanzan | **Confirmado**: ~570-620 ms extremo a extremo para un `STAT` completo. El default planeado de `--ble-timeout-s 10.0` tiene margen de sobra |
| **[Bug real, 2026-09-09, hardware real]** `INITF` puede tardar mucho más que otros comandos en completar su respuesta | Con `--ble-timeout-s`/`command_timeout_s` en 10.0 (default hasta acá), una calibración BLE podía fallar entre iteraciones con "Sin respuesta ... al comando 'INITF ...' tras 10.0 s de espera" pese a que el comando sí se procesó | **Confirmado y mitigado en la GUI** (`gui/workers.py`, `gui/views/connection_view.py`: subido a 20.0s). Causa: al arrancar la sesión, el módulo rankea de inmediato y, durante una ventana de transición, sus `SESSION_INFO_NTF` salen (además de por el canal de streaming dedicado) también por el canal de comandos normal — el mismo que entrega el eco multilínea de `INITF` y su `ok` de cierre. Esa competencia se midió demorando la respuesta completa hasta **~10.4s** en una corrida real (eco + bloque FiRa a los ~8.5s, `ok` final recién a los ~10.4s). La CLI (`app/cli.py`, flag `--ble-timeout-s`, default 10.0) no se tocó — un usuario que lo pise por CLI puede subirlo a mano si le pasa lo mismo |
| `qorvo off` — ¿corta la conexión BLE o solo apaga el módulo Qorvo? | No documentado en la especificación del firmware puente | pendiente de verificar |
| Reaparición del "eco pegado sin separador" ya visto en el bridge UART de J9 (`core/client.py`) | La lógica ya existe, pero nunca se ejerció con este puente | pendiente de verificar |
| Prompt del shell de Zephyr (`bt_nus:~$ `) intercalado en la respuesta | Hay que filtrarlo en `BleTransport`, igual que el eco por USB | **Confirmado** (2026-08-13, smoke test propio): aparece tras cada respuesta, ej. `'...\r\n\r\n\r\nbt_nus:~$ '` |
| `UART <DEC>` es exclusivo (USB↔pines), no aditivo — ver §7.1 | Provisionar `UART 1` deja inaccesible el USB nativo de esa misma placa, de forma persistente | **Confirmado** por `docs/referencia-comandos-fw110.md` §3.1; corregido el docstring/mensaje de `ble-provision` que lo subestimaba |
| Pairing Just Works — ¿requiere emparejamiento manual previo desde Windows? | Puede bloquear la conexión con un diálogo del sistema | **Descartado como bloqueante**: `bleak`/WinRT conectó sin ningún diálogo ni emparejamiento manual previo desde Windows (smoke test 2026-08-13) |
| Texto exacto del marcador de timeout del puente | Necesario para detectarlo y relanzarlo como `TransportError` | **Confirmado con hardware real** (2026-08-13): llega fragmentado en 3 notificaciones — `'Error: sin respues'` + `'ta del modul'` + `'o Qorvo (timeout)\r\n'` — y la duración real medida fue ~8.26 s (coincide con el límite duro de 8000 ms documentado) |
| **[Nuevo, no anticipado]** La conexión BLE se cae sola ~7-8 s después de la última actividad (éxito o timeout, mismo patrón en ambos casos) | `BleTransport` no puede asumir una conexión persistente de larga duración entre comandos; probablemente necesite reconectar por comando o tras inactividad | **Confirmado** (2026-08-13, smoke test propio, dos corridas): desconexión espontánea detectada por `disconnected_callback` ~7.7-7.9 s después del último dato recibido, en ambas corridas (una con timeout del bridge, otra con respuesta exitosa) — a investigar más en F8/F10 si es un supervision timeout de BLE o algo propio del firmware puente |
| `qorvo on` sin `-t`/`--time` deja el módulo encendido indefinidamente; con `-t 60s` se apaga solo | Si el módulo se apaga solo, cualquier comando posterior da timeout del puente aunque la placa y el puente estén bien | **Confirmado por observación**: un `qorvo stat` mandado minutos después de un `qorvo on --time 60s` (probado desde celular) dio el timeout de 8 s de arriba; al mandar `qorvo on` (sin límite) antes, `qorvo stat` funcionó de inmediato. `BleTransport`/GUI deberían encender explícitamente antes de operar, no asumir que el módulo ya está alimentado |
| Reconexión ante un corte BLE (a diferencia de `SerialLink`, que nunca reconecta sola) | Dado que la conexión se cae sola cada ~7-8s de inactividad (fila de arriba), *no* reconectar habría roto cualquier secuencia de comandos con pausas | **Decisión deliberada, implementada**: `write_line()`/`read_notification_line()` reconectan automáticamente si detectan la conexión caída (`_ensure_connected()`), a diferencia de `SerialLink`. Documentado como desvío consciente en `transport/ble_link.py` y probado sin hardware (`test_reconnects_automatically_after_disconnect`). **[Bug real, corregido, ver §7.3]** La reconexión automática no reactivaba el streaming BLE dedicado — corregido: `_ensure_connected()` llama a `enable_stream()` tras cada reconexión, no solo la primera vez en `open()`. Medido en F10 y de nuevo en la campaña de streaming: reconexión típica ~7-9s, sin pérdida de datos posterior |
| **[Bug real, corregido]** `power_on()`/`power_off()` no leían su propia respuesta (`"Qorvo status changed to: ..."`, sin marcador `ok`) | La línea quedaba en la cola y el siguiente comando real la heredaba como si fuera su propia respuesta — rompió el parseo de `STAT` en la primera prueba con hardware real | **Confirmado y corregido** (2026-08-13): `power_on()`/`power_off()` ahora drenan su respuesta con `_drain_response()` antes de devolver el control — ver §7.2 |
| **[Bug real, corregido]** `quiet_period_s=0.3` (default de USB) insuficiente para BLE — se midieron gaps de ~590ms entre fragmentos de una respuesta sana | Cortaba la lectura a mitad de respuesta, con el mismo síntoma que el bug de arriba | **Confirmado y corregido** (2026-08-13): `DwmCliClient` ahora acepta `quiet_period_s` en el constructor; `app/cli.py` usa `1.5s` para clientes BLE (`_BLE_QUIET_PERIOD_S`) — ver §7.2 |
| Escritura de `RESPF`/`INITF` completo (~130+ caracteres) y de `CALKEY <clave> <valor>` | Necesario para calibración y para reconfigurar direccionamiento FiRa | **[CRÍTICO, RESUELTO]** Confirmado con hardware real (F10, 2026-08-13, primera tanda) que **no era confiable**: `CALKEY <clave> <valor>` falló 0/5-6 intentos; `RESPF` con parámetros completos funcionó 1/7 veces y falló las 6 siguientes, incluso tras power-cycle físico del nRF52840. Descartado exhaustivamente como causa del lado cliente. **El usuario actualizó el firmware del puente nRF52840 y el problema desapareció**: segunda tanda de F10, mismo día, `CALKEY` 4/4 y `RESPF` consistente en todas las corridas — ver `docs/resultados-verificacion-ble.md` §3.4 |
| **[Incidente real, recuperado]** Una escritura de `CALKEY` reportada como `CommandTimeoutError` del lado del cliente en realidad se ejecutó en el firmware | `ant0.ch9.ant_delay` quedó en `0` (valor inútil para rangear) sin que el cliente lo supiera | **Ocurrió y se recuperó** (F10, 2026-08-13): detectado por relectura con `LISTCAL`; recuperado con `RESTORE` (autorización explícita del usuario), quedó en 16376 — ver `docs/resultados-verificacion-ble.md` §6.1. **No asumir que un timeout de `CALKEY` significa que no se escribió nada** |
| **[CRÍTICO, sin resolver del todo]** Crash intermitente de `dwm-gui` al conectar el RESPONDER por BLE (`ConnectionView._connect_responder` → `BleTransport.open()`) | Deja la GUI inutilizable para BLE en una fracción de los intentos — bloquea el uso normal de la ventana con el puente nRF52840 | **Diagnosticado a fondo, mitigado parcialmente, no eliminado** (2026-08-25, contra hardware real: COM25 + `FD:7A:90:57:CC:9F`). Sintoma: la app se cierra sin ninguna traza de Python, sin excepción capturada por `faulthandler`, sin evento de Windows "Application Error". Con `ProcDump` (Sysinternals) capturado el volcado real: el proceso termina con exit code **`0xC0000409`** (`STATUS_STACK_BUFFER_OVERRUN`, mecanismo *Fail Fast* de Windows) — no es una excepción de Python ni un access violation común, es la capa nativa de `winrt-runtime` (usada por `bleak` para hablar WinRT) abortando el proceso al detectar corrupción de stack, saltándose el manejo normal de excepciones. Ocurre específicamente al conectar BLE desde una app Qt (no se reprodujo nunca en la CLI de un solo hilo de F10). Probado con Python 3.14.4 (el intérprete de este banco): crashea de forma consistente. Con Python 3.12.10 (venv paralelo de diagnóstico): el mismo flujo a veces conecta bien, a veces falla con una excepción real capturable (`OSError` de bleak/WinRT, "sesión GATT cerrada a mitad del descubrimiento de servicios"), y a veces sigue crasheando igual — o sea, **es una condición de carrera real en la capa nativa, no 100% determinística, presente (con distinta frecuencia) en ambas versiones de Python probadas**. Mitigaciones aplicadas, todas correctas y necesarias pero **ninguna elimina la corrupción de stack de fondo**: `bleak.backends.winrt.util.allow_sta()` al arrancar `dwm-gui` (ajuste documentado por `bleak` para apps STA/GUI en Windows); `BleTransport` ya no toca el objeto COM `is_connected`/`mtu_size` desde un hilo distinto al suyo (mantiene copias planas actualizadas solo desde su propio hilo); los workers de la GUI (`ConnectWorker` y el resto) ahora atrapan cualquier excepción, no solo `Dwm3001cError`, así que cuando el connect BLE falla con una excepción real (en vez de crashear duro) la UI muestra el error en vez de quedarse trabada en "Conectando..." para siempre. **Recomendación práctica hasta que se resuelva río arriba (probable bug en `winrt-runtime`/pywinrt)**: si falla o crashea al conectar BLE, reintentar — no es determinístico, un segundo intento puede funcionar. Reportar como posible issue en `pywinrt/pywinrt` si se confirma en otra máquina/versión. **[Confirmado, 2026-09-09, hardware real]** Tras agregar `read_battery_level()`/`read_bridge_firmware_version()` automáticas justo después de conectar (ver §7.4), el cierre silencioso al conectar el segundo nodo desde "Conexión" se volvió notoriamente más frecuente (4 reproducciones seguidas contra hardware real). Se instrumentó el proceso real (no el stub `dwm-gui.exe`, que es solo un lanzador sin Python/Qt/bleak cargados — el proceso real es el `python.exe` hijo) con `ProcDump` (`-e 1 -ma -t -x`, monitoreo de excepciones activado) y `WinDbg`/`cdb.exe`: el volcado capturado en el momento exacto del cierre muestra `!analyze -v` con `ExceptionCode 0x80000003` (breakpoint, un artefacto del propio mecanismo de ProcDump para pausar-y-volcar en la terminación) pero **el log de ProcDump registra el código de salida real del proceso: `0xc0000409`** — confirma que sigue siendo el mismo `STATUS_STACK_BUFFER_OVERRUN` de esta fila, no un bug nuevo en el código de batería/firmware, sino ese código agregando actividad WinRT nativa concurrente (dos hilos de `BleTransport`, uno por nodo) justo en la ventana de conexión donde la condición de carrera es más probable. **Mitigación aplicada** (ver §7.4): las lecturas de batería/firmware dejaron de ser automáticas — ahora son una acción manual y serializada (`ConnectionView`: botón "Ver info" por rol, un nodo a la vez; `BlePairCalibrationWorker`: recién después de que ambas conexiones están estables, nunca en paralelo). No elimina la causa raíz — sigue pendiente confirmar con el usuario, contra hardware real y sin ninguna automatización de mouse/teclado de por medio, si reduce la frecuencia del crash |

> **Nota sobre el smoke test:** las filas marcadas "smoke test propio" (2026-08-13)
> se hicieron con un script descartable (no versionado, `bleak==3.0.2` sobre
> Python 3.14.4/WinRT en esta PC), instalado temporalmente para validar el
> diseño antes de escribir `BleTransport` (F8). Dirección BLE de la placa
> RESPONDER de este banco: `FD:7A:90:57:CC:9F`, advertising como `UWB Node`.

## 9. Referencias

- Firmware del puente: `I-mop-nrf52840-fw/doc/00_BLE_Protocol_Specification.md`
  (Nordic UART Service, comando `qorvo <texto>`, UUIDs de servicio/RX/TX).
- Arquitectura y reglas de capas de este proyecto: [arquitectura.md](arquitectura.md),
  [`CLAUDE.md`](../CLAUDE.md).
