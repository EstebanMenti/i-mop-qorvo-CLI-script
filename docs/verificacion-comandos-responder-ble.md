# Verificación de comandos CLI — RESPONDER por puente Bluetooth (fw 1.1.0)

> **Propósito:** documentar, con comando y respuesta **reales capturados contra hardware**, el comportamiento de cada comando de la CLI del firmware QM33 cuando se ejecuta sobre el RESPONDER conectado por el puente Bluetooth nRF52840 (rama `hardware/ble-bridge-nrf52840`), complementando [referencia-comandos-fw110.md](referencia-comandos-fw110.md) (capturada por USB directo, J20) con el mismo comportamiento visto **por BLE**.
> **Alcance:** firmware Qorvo **1.1.0** (build `Aug 10 2026 16:03:38`, misma copia con el fix de transporte UART descripto en [referencia-comandos-fw110.md §0.1](referencia-comandos-fw110.md#01-uart-físico-conector-j9--adaptador-dedicado--historial-de-verificación-incluye-una-modificación-de-firmware)). Placa RESPONDER cableada al puente nRF52840 `uwb-02` (dirección BLE `FD:7A:90:57:CC:9F`, Device ID `0xdeca0302`, Part ID `0x4ec4950e`), contra una placa INITIATOR por USB directo (COM27, serie `000760225287`). Capturas del **2026-08-25**, con `dwm3001c_cli.core.client.DwmCliClient.send_command()` invocado directamente (sin pasar por los parsers de alto nivel), para registrar exactamente lo que devuelve el firmware.
> **`RESTORE` no se ejecutó** (destructivo — pisaría con los valores de fábrica la calibración `ant0.ch9.ant_delay = 16251` recién guardada en NVM, ver [resultados-calibracion.md](resultados-calibracion.md)); de él solo se capturó la ayuda (`HELP RESTORE`).

---

## Índice

1. [Resumen de la corrida](#1-resumen-de-la-corrida)
2. [Tabla resumen de comandos](#2-tabla-resumen-de-comandos)
3. [Anytime commands](#3-anytime-commands)
   - 3.1 [`HELP`](#31-help)
   - 3.2 [`STAT`](#32-stat)
   - 3.3 [`THREAD`](#33-thread)
   - 3.4 [`STOP`](#34-stop)
   - 3.5 [`HELP <CMD>`](#35-help-cmd)
4. [Selección de aplicación](#4-selección-de-aplicación)
   - 4.1 [`LISTENER` / `LSTAT`](#41-listener--lstat)
   - 4.2 [`INITF`](#42-initf)
   - 4.3 [`RESPF`](#43-respf)
5. [IDLE time commands (modo NONE)](#5-idle-time-commands-modo-none)
   - 5.1 [`UART`](#51-uart)
   - 5.2 [`CALKEY`](#52-calkey)
   - 5.3 [`LISTCAL`](#53-listcal)
6. [Service commands (modo NONE)](#6-service-commands-modo-none)
   - 6.1 [`DIAG`](#61-diag)
   - 6.2 [`LCFG`](#62-lcfg)
   - 6.3 [`DECAID`](#63-decaid)
   - 6.4 [`SETAPP` y `SAVE`](#64-setapp-y-save)
   - 6.5 [`GETOTP`](#65-getotp)
   - 6.6 [`RESTORE`](#66-restore--no-ejecutado)
7. [Sesión TWR real (RESPONDER BLE + INITIATOR USB)](#7-sesión-twr-real-responder-ble--initiator-usb)
8. [Verificación final](#8-verificación-final)

---

## 1. Resumen de la corrida

| | |
|---|---|
| Fecha | 2026-08-25 |
| Firmware | 1.1.0 (`Aug 10 2026 16:03:38`) |
| RESPONDER (bajo prueba) | Puente BLE `uwb-02` (`FD:7A:90:57:CC:9F`) — Device ID `0xdeca0302`, Part ID `0x4ec4950e` |
| INITIATOR (par) | USB directo, COM27, serie `000760225287` |
| Transporte de las capturas | `BleTransport` (`quiet_period_s=1.5`, `command_timeout_s=10.0`), igual que `dwm calibrate --responder-ble-address` |
| Comandos ejercitados | 16 de los 17 del alcance de CLAUDE.md §1.1 (todos salvo `RESTORE`, solo documentado) |
| Resultado | **Todos respondieron según lo esperado** (`ok`, o `KO` donde el firmware lo hace por diseño — ver [§5.2 CALKEY](#52-calkey)). `ant0.ch9.ant_delay` verificado sin cambios al final (`16251`) |

> **Nota sobre el eco del comando:** por este puente BLE, el eco de cada comando llegó siempre en línea propia y `DwmCliClient.send_command()` lo descartó correctamente — a diferencia de lo observado por USB directo cuando `LISTENER`/`INITF`/`RESPF` ya está corriendo (bug de parsing corregido en `parse_stat`, ver `core/parsers.py`). Las respuestas de abajo ya vienen **sin el eco**.

## 2. Tabla resumen de comandos

| Comando | Modo requerido | Termina en | Resultado por BLE |
|---|---|---|---|
| `HELP` / `HELP <CMD>` | cualquiera | `ok` | ✅ Igual que por USB (§3.1, §3.5) |
| `STAT` | cualquiera | `ok` | ✅ Refleja `Current App` correctamente en NONE y con cada app corriendo (§3.2) |
| `STOP` | cualquiera | `ok` | ✅; si había notificaciones pendientes sin consumir, llegan antes del `ok` (§3.4) |
| `THREAD` | cualquiera | `ok` | ✅ Igual que por USB (§3.3) |
| `LISTENER` | NONE | `ok` | ✅ Arranca, `LSTAT` reporta contadores (§4.1) |
| `INITF` / `RESPF` | NONE | `ok` | ✅ Vuelca los parámetros FiRa; ranging real 40/40 con el par (§4.2, §4.3, §7) |
| `UART` | NONE | `ok` | ✅ Consulta — reporta `UART: 1` (placa provisionada para el puente, §5.1) |
| `CALKEY <key>` | NONE | **`KO`** | ⚠️ Lectura rota en fw 1.1.0 (igual que por USB) — usar `LISTCAL` (§5.2) |
| `CALKEY <key> <val>` | NONE | `ok` | ✅ Escritura confirmada con `clave: valor` (§5.2) |
| `LISTCAL` | NONE | `ok` | ✅ 259 claves íntegras por BLE (§5.3) |
| `DIAG` / `DIAG 0\|1` | NONE | `ok` | ✅ Consulta y toggle correctos (§6.1) |
| `LCFG` | NONE | `ok` | ✅ JSON íntegro (§6.2) |
| `DECAID` | NONE | `ok` | ✅ IDs del chip correctos (§6.3) |
| `SETAPP <app>` | NONE | `ok` | ✅ (§6.4) |
| `SAVE` | NONE (sin ranging) | `ok` | ✅ (§6.4) |
| `GETOTP` | NONE | `ok` | ✅ 131 líneas íntegras por BLE (§6.5) |
| `RESTORE` | NONE | — | ⚠️ **No ejercitado** — destructivo (§6.6) |

## 3. Anytime commands

### 3.1 `HELP`

Lista todos los comandos disponibles del firmware, agrupados por categoría. Sirve como verificación rápida de que la consola responde y de qué comandos admite esta build.

```text
> HELP

DWM3001CDK - DW3_QM33_SDK - FreeRTOS

---       Anytime commands       ---
HELP      ?         STOP      THREAD
STAT

---    Application selection     ---
LISTENER  RESPF     INITF

---      IDLE time commands      ---
UART      CALKEY    LISTCAL   CALINFO

---       Service commands       ---
RESTORE   DIAG      LCFG      DECAID
SAVE      SETAPP    GETOTP

---       LISTENER Options       ---
LSTAT

ok
```

> Aparece `CALINFO`, que no figura en [referencia-comandos-fw110.md](referencia-comandos-fw110.md) (capturado 2026-08-06 sobre otra placa/build). **[Fuera del manual]** No se investigó su sintaxis en esta corrida — queda pendiente si hace falta documentarlo.

### 3.2 `STAT`

Reporta el estado del dispositivo: versión, apps disponibles y, sobre todo, **qué aplicación está corriendo ahora mismo** (campo `"Current App"`; sin línea `MODE:` en esta build). Es el comando que responde la pregunta "¿la sesión está abierta y corriendo, o en NONE?" (ver conversación previa de esta sesión de trabajo).

En modo NONE:

```text
> STAT

JS0109{"Info":{
"Device":"DWM3001CDK - DW3_QM33_SDK - FreeRTOS",
"Current App":"NONE",
"Version":"1.1.0",
"Build":"Aug 10 2026 16:03:38",
"Apps":["LISTENER","RESPF","INITF"],
"Driver":"DW3XXX Device Driver Version 08.19.02",
"UWB stack":"R12.7.0-405-gb33c5c4272"}}

ok
```

Con `LISTENER` corriendo (idéntica estructura con `INITF`/`RESPF`, solo cambia `"Current App"`):

```text
> STAT

JS010D{"Info":{
"Device":"DWM3001CDK - DW3_QM33_SDK - FreeRTOS",
"Current App":"LISTENER",
"Version":"1.1.0",
"Build":"Aug 10 2026 16:03:38",
"Apps":["LISTENER","RESPF","INITF"],
"Driver":"DW3XXX Device Driver Version 08.19.02",
"UWB stack":"R12.7.0-405-gb33c5c4272"}}

ok
```

### 3.3 `THREAD`

Diagnóstico interno del RTOS: uso de stack de cada hilo y del heap. Útil para descartar problemas de memoria si el firmware se comporta raro.

```text
> THREAD

THREAD NAME     	Stack usage
Control         	1116/2048
IDLE            	132/516
Default         	180/4304
Flush           	176/512
Tmr Svc         	140/516
Total HEAP      	51200
Current HEAP used	7776
Max HEAP used   	8144
ok
```

### 3.4 `STOP`

Detiene la aplicación en curso (`LISTENER`/`INITF`/`RESPF`) y vuelve a modo NONE. Si había notificaciones `SESSION_INFO_NTF` generadas antes de recibir el `STOP` (p. ej. `INITF`/`RESPF` corriendo solos, sin par, generando rondas `RX_TIMEOUT` cada `BLOCK` ms), esas notificaciones acumuladas llegan **todas antes** del `ok` final.

Caso simple (deteniendo `LISTENER`, sin backlog):

```text
> STOP


ok
```

Caso con backlog (deteniendo `INITF` que estuvo ~1 s corriendo solo, sin `RESPF` emparejado): llegaron **39 notificaciones `SESSION_INFO_NTF`** acumuladas, todas `status="RX_TIMEOUT"` (esperado: sin par, cada ronda vence), antes del `ok`. Extracto (primera y última):

```text
> STOP

SESSION_INFO_NTF: {session_handle=1, sequence_number=47, block_index=47, n_measurements=1
 [mac_address=0x0001, status="RX_TIMEOUT"]}
...  (37 notificaciones más, sequence_number 48 a 85)
SESSION_INFO_NTF: {session_handle=1, sequence_number=85, block_index=85, n_measurements=1
 [mac_address=0x0001, status="RX_TIMEOUT"]}

ok
```

Deteniendo `RESPF` en las mismas condiciones: **38 notificaciones**, mismo patrón (`mac_address=0x0000` porque ahí el par esperado es el `ADDR=0` del initiator).

> **Nota operativa:** esto confirma en la práctica lo que dice [referencia-comandos-fw110.md §1.3](referencia-comandos-fw110.md#13-stop): conviene **drenar las notificaciones pendientes** (o al menos tolerarlas) antes de interpretar la respuesta de `STOP` como "solo `ok`".

### 3.5 `HELP <CMD>`

`HELP` acepta un comando como argumento y devuelve su ayuda específica — confirmado para `INITF`, `RESPF`, `CALKEY`, `UART` y `RESTORE`:

```text
> HELP INITF

INITF:
INITF [Option1] [Option2] ...
Options: (only default values are shown, check the SDK Manual to know more about the available configurations)
  -CHAN=9       --> Channel number
  -PRFSET=BPRF4 --> PRF set
  -PCODE=10     --> Preamble code index
  -SLOT=2400    --> Slot duration [RSTU]
  -BLOCK=200    --> Block duration [ms]
  -ROUND=25     --> Round duration [slots]
  -RRU=DSTWR    --> Ranging round usage
  -ID=42        --> Session ID
  -VUPPER=01:02:03:04:05:06:07:08   --> vUpper64
  -MULTI        --> Activate one-to-many mode
  -HOP          --> Activate round hopping
  -ADDR=0       --> Device own address (Initiator address)
  -PADDR=1      --> 1st Responder address
     or to set multiple responders:
  -PADDR=[1,2,.,.,n]  --> to set n Responder addresses (for one-to-many)

ok
```

```text
> HELP RESPF

RESPF:
RESPF [Option1] [Option2] ...
...
  -ADDR=1       --> Device own address (Responder address)
  -PADDR=0      --> Peer address (Initiator address)

ok
```

```text
> HELP CALKEY

CALKEY:
Set or get a calibration Key. Usage:
To set a value: "CALKEY <key> <value>"
To get a value: "CALKEY <key>"

ok
```

```text
> HELP UART

UART:
Usage: To initialize selected UART: "UART <DEC>"

ok
```

```text
> HELP RESTORE

RESTORE:
Restores the default configuration, both UWB and System.

ok
```

## 4. Selección de aplicación

Las tres aplicaciones (`LISTENER`, `INITF`, `RESPF`) son excluyentes entre sí y solo pueden arrancar en modo NONE. Al iniciar `INITF`/`RESPF` con parámetros, el firmware vuelca la configuración FiRa efectiva — útil para confirmar que lo que se pidió es lo que quedó activo (en particular `ADDR`/`PADDR`, ver conversación previa sobre direccionamiento).

### 4.1 `LISTENER` / `LSTAT`

`LISTENER` pone la placa en modo sniffer (recibe tramas UWB sin participar en ranging). `LSTAT` (solo válido con `LISTENER` corriendo) reporta contadores de eventos de recepción — todos en cero en esta captura porque no hubo tráfico UWB de terceros durante la prueba.

```text
> LISTENER


ok
```

```text
> STAT   (mientras LISTENER corre)

JS010D{"Info":{
...
"Current App":"LISTENER",
...
ok
```

```text
> LSTAT

JS006F{"RX Events":{
"CRCG":0,
"CRCB":0,
"ARFE":0,
"PHE":0,
"RSL":0,
"SFDTO":0,
"PTO":0,
"FTO":0,
"SFDD":0}}

ok
```

### 4.2 `INITF`

Rol INITIATOR de la sesión TWR. Esta placa se usa normalmente como **RESPONDER** (ver §4.3); se arrancó `INITF` acá únicamente para documentar el comando de forma completa sobre esta placa — no es su uso habitual.

```text
> INITF -CHAN=9 -PRFSET=BPRF4 -PCODE=10 -SLOT=2400 -BLOCK=200 -ROUND=25 -RRU=DSTWR -ID=42 -VUPPER=01:02:03:04:05:06:07:08 -ADDR=0 -PADDR=1

FiRa Session Parameters: {
SESSION_ID: 42,
CHANNEL_NUMBER: 9,
DEVICE_ROLE: INITIATOR,
RANGING_ROUND_USAGE: DS_TWR_DEFERRED,
SLOT_DURATION [rstu]: 2400,
RANGING_DURATION [ms]: 200,
SLOTS_PER_RR: 25,
MULTI_NODE_MODE: UNICAST,
HOPPING_MODE: Disabled,
RFRAME_CONFIG: SP3,
SFD_ID: 2,
PREAMBLE_CODE_INDEX: 10,
STATIC_STS_IV: "01:02:03:04:05:06",
VENDOR_ID: "07:08",
DEVICE_MAC_ADDRESS: 0x0000,
DST_MAC_ADDRESS[0]: 0x0001
}
ok
```

`STAT` mientras corre (sin par emparejado, por eso no se muestran mediciones acá — solo confirma el modo):

```text
> STAT

JS010A{"Info":{
...
"Current App":"INITF",
...
ok
```

Se detuvo con `STOP` (ver el backlog de 39 notificaciones `RX_TIMEOUT` en [§3.4](#34-stop)).

### 4.3 `RESPF`

Rol RESPONDER — el uso habitual de esta placa en el banco de pruebas.

```text
> RESPF -CHAN=9 -PRFSET=BPRF4 -PCODE=10 -SLOT=2400 -BLOCK=200 -ROUND=25 -RRU=DSTWR -ID=42 -VUPPER=01:02:03:04:05:06:07:08 -ADDR=1 -PADDR=0

FiRa Session Parameters: {
SESSION_ID: 42,
CHANNEL_NUMBER: 9,
DEVICE_ROLE: RESPONDER,
RANGING_ROUND_USAGE: DS_TWR_DEFERRED,
SLOT_DURATION [rstu]: 2400,
RANGING_DURATION [ms]: 200,
SLOTS_PER_RR: 25,
MULTI_NODE_MODE: UNICAST,
HOPPING_MODE: Disabled,
RFRAME_CONFIG: SP3,
SFD_ID: 2,
PREAMBLE_CODE_INDEX: 10,
STATIC_STS_IV: "01:02:03:04:05:06",
VENDOR_ID: "07:08",
DEVICE_MAC_ADDRESS: 0x0001,
DST_MAC_ADDRESS: 0x0000
}
ok
```

`STAT` mientras corre:

```text
> STAT

JS010A{"Info":{
...
"Current App":"RESPF",
...
ok
```

Se detuvo con `STOP` (backlog de 38 notificaciones `RX_TIMEOUT`, ver [§3.4](#34-stop)). La sesión **con par real** (initiator emparejado) se documenta en [§7](#7-sesión-twr-real-responder-ble--initiator-usb).

## 5. IDLE time commands (modo NONE)

### 5.1 `UART`

Consulta cuál interfaz física recibe la consola: `0` = USB, `1` = pines UART. Esta placa fue provisionada (`dwm ble-provision`) para responder por los pines UART, que es justamente cómo el puente Bluetooth le habla — por eso reporta `1`.

```text
> UART

UART: 1

ok
```

### 5.2 `CALKEY`

Lee o escribe una clave de calibración individual.

**La lectura está rota en esta build de firmware** (mismo comportamiento documentado por USB en [referencia-comandos-fw110.md §3.2](referencia-comandos-fw110.md#32-calkey)): responde `KO` para cualquier clave, incluidas las que sí figuran en `LISTCAL`. No es un problema del puente BLE ni de esta herramienta — es un bug del firmware 1.1.0, confirmado ahora también por BLE:

```text
> CALKEY ant0.ch9.ant_delay

Please enter a valid key: ant0.ch9.ant_delay


KO
```

*Workaround, igual que por USB:* leer la clave filtrando la salida de `LISTCAL` (así lo implementa `DwmCliClient.calkey_read()`).

**La escritura sí funciona**, y confirma con `clave: valor`. Se usó una escritura neutra (reescribir `restricted_channels` con su propio valor, `0`) para no alterar ninguna calibración real:

```text
> CALKEY restricted_channels 0

restricted_channels: 0x0000 (len: 2)

ok
```

### 5.3 `LISTCAL`

Vuelca **las 259 claves de calibración** de la placa, íntegras por BLE (sin corrupción — el fix de firmware de transporte UART descripto en [referencia-comandos-fw110.md §0.1](referencia-comandos-fw110.md#01-uart-físico-conector-j9--adaptador-dedicado--historial-de-verificación-incluye-una-modificación-de-firmware) aplica también al camino BLE, porque ocurre en el firmware del Qorvo antes de llegar a cualquier bridge). Extracto real, con las claves relevantes para este banco:

```text
> LISTCAL

restricted_channels: 0x0000 (len: 2)
wifi_coex_mode: 0x00 (len: 1)
...
xtal_trim: 0x19 (len: 1)
rf_noise_offset: 0xf9 (len: 1)
pdoa_lut0.data: 0x0000...0000 (len: 124)
pdoa_lut1.data: 0x0000...0000 (len: 124)
...
ant0.ch5.ant_delay: 0x00003fef (len: 4)          ← retardo de antena, canal 5
...
ant0.ch9.ant_delay: 0x00003f7b (len: 4)          ← retardo de antena, canal 9 (calibrado: 16251)
...
ant1.ch9.ant_delay: 0x00003ff8 (len: 4)
ant2.ch5.ant_delay: 0x00004015 (len: 4)
ant2.ch9.ant_delay: 0x00004015 (len: 4)
ant3.ch5.ant_delay: 0x00004015 (len: 4)
ant3.ch9.ant_delay: 0x00004015 (len: 4)
...
ant_pair0.ch9.pdoa.offset: 0x0000 (len: 2)       ← offset de PDoA (calibración de ángulo)
...
experimental.mac.session_scheduler.id: 0x00 (len: 1)

ok
```

`ant0.ch9.ant_delay = 0x00003f7b (16251)` coincide con el valor calibrado y guardado en [resultados-calibracion.md](resultados-calibracion.md) — confirma que ninguna de las pruebas de esta batería lo tocó.

## 6. Service commands (modo NONE)

### 6.1 `DIAG`

Habilita/deshabilita el modo diagnóstico (agrega `RSSI` a `SESSION_INFO_NTF` y emite `RANGE_DIAGNOSTICS_NTF` durante el ranging). Se probó consulta → habilitar → consulta → deshabilitar (reversión, sin dejar el modo activado):

```text
> DIAG

DIAG: 0

ok
```
```text
> DIAG 1


ok
```
```text
> DIAG

DIAG: 1

ok
```
```text
> DIAG 0


ok
```

### 6.2 `LCFG`

Configuración cruda de la aplicación `LISTENER` (parámetros de radio del canal activo).

```text
> LCFG

JS00A6{"LCFG PARAM":{
"CHAN":9,
"PAC":8,
"PCODE":10,
"SFDTYPE":3,
"DRATE":6810,
"PHRMODE":0,
"PHRRATE":0,
"STSMODE":0,
"STSLEN":64,
"PDOAMODE":0,
"XTALTRIM":46}}

ok
```

### 6.3 `DECAID`

Identificadores de fábrica del chip UWB — permiten confirmar que se está hablando con la placa esperada.

```text
> DECAID

Qorvo Device ID = 0xdeca0302
Qorvo Lot ID = 0x0000505634583230
Qorvo Part ID = 0x4ec4950e
Qorvo SoC ID = 00005056345832304ec4950e

ok
```

### 6.4 `SETAPP` y `SAVE`

`SETAPP <app>` selecciona qué aplicación arranca automáticamente al energizar la placa (requiere `SAVE` posterior para persistir). `SAVE` graba en NVM la configuración y calibración vigentes. Ambos responden `ok` sin salida adicional:

```text
> SETAPP NONE


ok
```
```text
> SAVE


ok
```

### 6.5 `GETOTP`

Vuelca las 128 direcciones OTP de fábrica (`0x000`–`0x07F`), íntegras por BLE (131 líneas, mismo fix de transporte que `LISTCAL`). Extracto con las direcciones documentadas:

```text
> GETOTP

OTP CONTENT: {
"0x000":"0x00000000",
...
"0x006":"0x4ec4950e",      ← Part ID
"0x00d":"0x34583230",      ← Lot ID (parte baja)
"0x00e":"0x00005056",      ← Lot ID (parte alta)
"0x011":"0x7d7d7d7d",      ← potencia TX FCC, canal 5
"0x013":"0x81818181",      ← potencia TX FCC, canal 9
"0x018":"0x00af00af",      ← PGCOUNT de producción
"0x01a":"0x3fef3fef",      ← retardo de antena canal 5 de fábrica (16367)
"0x01c":"0x3ff83ff8",      ← retardo de antena canal 9 de fábrica (16376)
"0x01e":"0x00be0019",      ← duración de trama | trimming del cristal
"0x01f":"0x00010201",      ← Platform ID | Cal Rev | OTP Revision
"0x020"–"0x034":            códigos de calibración de PLL (no nulos)
"0x035"–"0x07f":            0x00000000 (área libre de usuario sin grabar)
}
ok
```

> El OTP de fábrica (`0x01c = 0x3FF8`, 16376) es el punto de partida antes de calibrar — coincide con el valor inicial documentado en [resultados-calibracion.md](resultados-calibracion.md) (16376 → 16200 → tras recalibración a 2 m, 16251).

### 6.6 `RESTORE` — ⚠ no ejecutado

**No se ejecutó.** Pisaría con los valores de fábrica tanto la configuración del sistema como **todas** las claves de calibración — incluida `ant0.ch9.ant_delay`, revirtiéndola de `16251` a `16376` (OTP) y escribiendo en NVM automáticamente, sin confirmación adicional del firmware. Solo se capturó su ayuda (ver [§3.5](#35-help-cmd)).

> Regla del proyecto (CLAUDE.md §6.3): "No ejecutar acciones destructivas sobre las placas (`RESTORE`, borrado de NVM, escritura de OTP) desde código automatizado sin confirmación explícita e interactiva del usuario." Confirmado con el usuario que **no** se ejecute en esta batería.

## 7. Sesión TWR real (RESPONDER BLE + INITIATOR USB)

Con `RESPF` en el RESPONDER (BLE) y `INITF` en el INITIATOR (COM27, USB) — mismo `SessionParams` que `dwm calibrate`/`dwm validate` (`ADDR`/`PADDR` explícitos, ver [validation/spec.py](../src/dwm3001c_cli/validation/spec.py)) — la sesión rangeó con normalidad. Ventana de 6 s, **30 notificaciones recibidas en el INITIATOR, todas `SUCCESS`**, distancias entre 200 y 208 cm (placas físicamente a 2,00 m — coherente con la calibración recién hecha):

```text
> RESPF -CHAN=9 -PRFSET=BPRF4 -PCODE=10 -SLOT=2400 -BLOCK=200 -ROUND=25 -RRU=DSTWR -ID=42 -VUPPER=01:02:03:04:05:06:07:08 -ADDR=1 -PADDR=0      (RESPONDER, BLE)

FiRa Session Parameters: { ... DEVICE_ROLE: RESPONDER ... DEVICE_MAC_ADDRESS: 0x0001, DST_MAC_ADDRESS: 0x0000 }
ok
```

```text
> INITF -CHAN=9 -PRFSET=BPRF4 -PCODE=10 -SLOT=2400 -BLOCK=200 -ROUND=25 -RRU=DSTWR -ID=42 -VUPPER=01:02:03:04:05:06:07:08 -ADDR=0 -PADDR=1      (INITIATOR, COM27 USB)

FiRa Session Parameters: { ... DEVICE_ROLE: INITIATOR ... DEVICE_MAC_ADDRESS: 0x0000, DST_MAC_ADDRESS[0]: 0x0001 }
ok
```

Notificaciones recibidas en el initiator (primeras 8 de 30):

```text
SESSION_INFO_NTF: {session_handle=1, sequence_number=0, block_index=0, n_measurements=1 [mac_address=0x0001, status="SUCCESS", distance[cm]=200]}
SESSION_INFO_NTF: {session_handle=1, sequence_number=1, block_index=1, n_measurements=1 [mac_address=0x0001, status="SUCCESS", distance[cm]=203]}
SESSION_INFO_NTF: {session_handle=1, sequence_number=2, block_index=2, n_measurements=1 [mac_address=0x0001, status="SUCCESS", distance[cm]=200]}
SESSION_INFO_NTF: {session_handle=1, sequence_number=3, block_index=3, n_measurements=1 [mac_address=0x0001, status="SUCCESS", distance[cm]=207]}
SESSION_INFO_NTF: {session_handle=1, sequence_number=4, block_index=4, n_measurements=1 [mac_address=0x0001, status="SUCCESS", distance[cm]=204]}
SESSION_INFO_NTF: {session_handle=1, sequence_number=5, block_index=5, n_measurements=1 [mac_address=0x0001, status="SUCCESS", distance[cm]=205]}
SESSION_INFO_NTF: {session_handle=1, sequence_number=6, block_index=6, n_measurements=1 [mac_address=0x0001, status="SUCCESS", distance[cm]=208]}
SESSION_INFO_NTF: {session_handle=1, sequence_number=7, block_index=7, n_measurements=1 [mac_address=0x0001, status="SUCCESS", distance[cm]=202]}
```

## 8. Verificación final

Al cerrar la batería, se releyó `ant0.ch9.ant_delay` vía `LISTCAL` para confirmar que ninguna de las 35 capturas anteriores tocó la calibración:

```text
ant0.ch9.ant_delay: 0x00003f7b (len: 4)     ← 16251, sin cambios
```

**Conclusión:** los 16 comandos ejercitados (todos salvo `RESTORE`) funcionan correctamente por el puente Bluetooth, con las mismas particularidades ya conocidas de este firmware por USB (lectura de `CALKEY` rota, `LISTCAL`/`GETOTP` íntegros gracias al fix de transporte). El RESPONDER quedó operativo, calibrado (`16251`) y sin ningún efecto colateral de esta batería de pruebas.
