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

El rol Bluetooth es **siempre RESPONDER**, nunca INITIATOR: la calibración y
la validación solo leen notificaciones `SESSION_INFO_NTF` del lado INITIATOR
(USB), nunca del lado RESPONDER — esto es compatible con una limitación del
puente nRF52840, que solo reenvía respuestas a lo que se le pide, no mensajes
espontáneos del Qorvo.

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
| F9 | GUI de escritorio PySide6 (`src/dwm3001c_cli/gui/`): conexión, terminal manual, validación y calibración con gráfico en vivo | **implementado, sin verificar aún contra hardware real** — tests con `pytest-qt` sobre `FakeTransport` en verde; falta el smoke test manual de la app (`dwm-gui`) con placas reales |
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

## 8. Riesgos e incertidumbres a verificar contra hardware real

No inventar comportamiento no documentado — esta tabla se actualiza con el
resultado real de F10.

| Riesgo | Por qué importa | Resultado |
|---|---|---|
| MTU efectivo con `bleak`/WinRT en Windows (no solo con una app de celular) | Si es insuficiente, las respuestas (o la escritura de `RESPF`/`INITF`) se truncan | **Confirmado** (2026-08-13): MTU negociado **247**; `STAT` **y `LISTCAL` completo (259 líneas, la respuesta más grande)** llegaron enteros, con la implementación de producción (`transport/ble_link.py`). Falta todavía confirmar la escritura saliente de un `RESPF`/`INITF` completo (~130+ caracteres) |
| Latencia real del puente | Define si los timeouts del cliente Python alcanzan | **Confirmado**: ~570-620 ms extremo a extremo para un `STAT` completo. El default planeado de `--ble-timeout-s 10.0` tiene margen de sobra |
| `qorvo off` — ¿corta la conexión BLE o solo apaga el módulo Qorvo? | No documentado en la especificación del firmware puente | pendiente de verificar |
| Reaparición del "eco pegado sin separador" ya visto en el bridge UART de J9 (`core/client.py`) | La lógica ya existe, pero nunca se ejerció con este puente | pendiente de verificar |
| Prompt del shell de Zephyr (`bt_nus:~$ `) intercalado en la respuesta | Hay que filtrarlo en `BleTransport`, igual que el eco por USB | **Confirmado** (2026-08-13, smoke test propio): aparece tras cada respuesta, ej. `'...\r\n\r\n\r\nbt_nus:~$ '` |
| `UART <DEC>` es exclusivo (USB↔pines), no aditivo — ver §7.1 | Provisionar `UART 1` deja inaccesible el USB nativo de esa misma placa, de forma persistente | **Confirmado** por `docs/referencia-comandos-fw110.md` §3.1; corregido el docstring/mensaje de `ble-provision` que lo subestimaba |
| Pairing Just Works — ¿requiere emparejamiento manual previo desde Windows? | Puede bloquear la conexión con un diálogo del sistema | **Descartado como bloqueante**: `bleak`/WinRT conectó sin ningún diálogo ni emparejamiento manual previo desde Windows (smoke test 2026-08-13) |
| Texto exacto del marcador de timeout del puente | Necesario para detectarlo y relanzarlo como `TransportError` | **Confirmado con hardware real** (2026-08-13): llega fragmentado en 3 notificaciones — `'Error: sin respues'` + `'ta del modul'` + `'o Qorvo (timeout)\r\n'` — y la duración real medida fue ~8.26 s (coincide con el límite duro de 8000 ms documentado) |
| **[Nuevo, no anticipado]** La conexión BLE se cae sola ~7-8 s después de la última actividad (éxito o timeout, mismo patrón en ambos casos) | `BleTransport` no puede asumir una conexión persistente de larga duración entre comandos; probablemente necesite reconectar por comando o tras inactividad | **Confirmado** (2026-08-13, smoke test propio, dos corridas): desconexión espontánea detectada por `disconnected_callback` ~7.7-7.9 s después del último dato recibido, en ambas corridas (una con timeout del bridge, otra con respuesta exitosa) — a investigar más en F8/F10 si es un supervision timeout de BLE o algo propio del firmware puente |
| `qorvo on` sin `-t`/`--time` deja el módulo encendido indefinidamente; con `-t 60s` se apaga solo | Si el módulo se apaga solo, cualquier comando posterior da timeout del puente aunque la placa y el puente estén bien | **Confirmado por observación**: un `qorvo stat` mandado minutos después de un `qorvo on --time 60s` (probado desde celular) dio el timeout de 8 s de arriba; al mandar `qorvo on` (sin límite) antes, `qorvo stat` funcionó de inmediato. `BleTransport`/GUI deberían encender explícitamente antes de operar, no asumir que el módulo ya está alimentado |
| Reconexión ante un corte BLE (a diferencia de `SerialLink`, que nunca reconecta sola) | Dado que la conexión se cae sola cada ~7-8s de inactividad (fila de arriba), *no* reconectar habría roto cualquier secuencia de comandos con pausas | **Decisión deliberada, implementada**: `write_line()` reconecta automáticamente si detecta la conexión caída (`_ensure_connected()`), a diferencia de `SerialLink`. Documentado como desvío consciente en `transport/ble_link.py` y probado sin hardware (`test_reconnects_automatically_after_disconnect`); falta medir en F10 la latencia real de una reconexión a mitad de una calibración larga |
| **[Bug real, corregido]** `power_on()`/`power_off()` no leían su propia respuesta (`"Qorvo status changed to: ..."`, sin marcador `ok`) | La línea quedaba en la cola y el siguiente comando real la heredaba como si fuera su propia respuesta — rompió el parseo de `STAT` en la primera prueba con hardware real | **Confirmado y corregido** (2026-08-13): `power_on()`/`power_off()` ahora drenan su respuesta con `_drain_response()` antes de devolver el control — ver §7.2 |
| **[Bug real, corregido]** `quiet_period_s=0.3` (default de USB) insuficiente para BLE — se midieron gaps de ~590ms entre fragmentos de una respuesta sana | Cortaba la lectura a mitad de respuesta, con el mismo síntoma que el bug de arriba | **Confirmado y corregido** (2026-08-13): `DwmCliClient` ahora acepta `quiet_period_s` en el constructor; `app/cli.py` usa `1.5s` para clientes BLE (`_BLE_QUIET_PERIOD_S`) — ver §7.2 |
| Escritura de `RESPF`/`INITF` completo (~130+ caracteres) y de `CALKEY <clave> <valor>` | Necesario para calibración y para reconfigurar direccionamiento FiRa | **[CRÍTICO, RESUELTO]** Confirmado con hardware real (F10, 2026-08-13, primera tanda) que **no era confiable**: `CALKEY <clave> <valor>` falló 0/5-6 intentos; `RESPF` con parámetros completos funcionó 1/7 veces y falló las 6 siguientes, incluso tras power-cycle físico del nRF52840. Descartado exhaustivamente como causa del lado cliente. **El usuario actualizó el firmware del puente nRF52840 y el problema desapareció**: segunda tanda de F10, mismo día, `CALKEY` 4/4 y `RESPF` consistente en todas las corridas — ver `docs/resultados-verificacion-ble.md` §3.4 |
| **[Incidente real, recuperado]** Una escritura de `CALKEY` reportada como `CommandTimeoutError` del lado del cliente en realidad se ejecutó en el firmware | `ant0.ch9.ant_delay` quedó en `0` (valor inútil para rangear) sin que el cliente lo supiera | **Ocurrió y se recuperó** (F10, 2026-08-13): detectado por relectura con `LISTCAL`; recuperado con `RESTORE` (autorización explícita del usuario), quedó en 16376 — ver `docs/resultados-verificacion-ble.md` §6.1. **No asumir que un timeout de `CALKEY` significa que no se escribió nada** |

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
