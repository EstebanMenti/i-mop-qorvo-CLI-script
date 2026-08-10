# Referencia de comandos CLI — respuestas reales del firmware 1.1.0

> **Propósito:** documentar cada comando admitido por el firmware CLI del DWM3001CDK con la **respuesta real capturada de la placa**, como complemento verificado de la [guía de referencia](referencias/guia-cli-calibracion-dwm3001cdk.md) (que sigue el manual del fabricante).
> **Alcance:** firmware **1.1.0** (build 13/08/2025, `DW3_QM33_SDK - FreeRTOS`). Capturas del 2026-08-06 sobre la placa serie `F55EA0AF0AC4` (COM26), obtenidas con la herramienta de este proyecto. `RESTORE` **no se ejecutó** (destructivo); de él solo se muestra la ayuda.

---

## 0. Convenciones de la consola (verificadas con hardware)

| Comportamiento | Detalle |
|---|---|
| Terminador de entrada | La placa acepta `\r\n` |
| Eco | La placa **repite el comando enviado** como parte de la salida (omitido en las capturas de abajo) |
| Fin de respuesta | Línea **`ok`** (éxito) o **`KO`** (error). Todo comando termina con uno de los dos |
| Bloques JSON | Prefijo `JSxxxx` (longitud en 4 dígitos hex) y contenido **partido en varias líneas** (`STAT`, `LCFG`, `LSTAT`, `GETOTP`) |
| Notificaciones | `SESSION_STATUS_NTF` y `SESSION_INFO_NTF` aparecen de forma asincrónica durante una sesión; `SESSION_INFO_NTF` llega **en dos líneas** (la continuación arranca con un `\r` residual) |
| Ayuda por comando | `HELP <CMD>` funciona para todos los comandos listados por `HELP` |

## 1. Anytime commands (ejecutables en cualquier momento)

### 1.1 `HELP` / `?`

```text
DWM3001CDK - DW3_QM33_SDK - FreeRTOS

---       Anytime commands       ---
HELP      ?         STOP      THREAD
STAT

---    Application selection     ---
LISTENER  RESPF     INITF

---      IDLE time commands      ---
UART      CALKEY    LISTCAL

---       Service commands       ---
RESTORE   DIAG      LCFG      DECAID
SAVE      SETAPP    GETOTP

---       LISTENER Options       ---
LSTAT

ok
```

### 1.2 `STAT`

Sin línea `MODE:` (a diferencia del manual): el modo se lee del campo `"Current App"`.

```text
JS0109{"Info":{
"Device":"DWM3001CDK - DW3_QM33_SDK - FreeRTOS",
"Current App":"NONE",
"Version":"1.1.0",
"Build":"Aug 13 2025 14:23:02",
"Apps":["LISTENER","RESPF","INITF"],
"Driver":"DW3XXX Device Driver Version 08.19.02",
"UWB stack":"R12.7.0-405-gb33c5c4272"}}

ok
```

Con una aplicación corriendo, solo cambia `"Current App"` (p. ej. `"LISTENER"`).

### 1.3 `STOP`

Responde `ok` (dos líneas vacías previas). Si había una sesión FiRa activa, las notificaciones acumuladas pueden aparecer antes del `ok`. **Nota operativa:** tras `STOP`, el firmware tarda una fracción de segundo en volver a NONE — un `STAT` inmediato puede reportar la app anterior.

### 1.4 `THREAD`

```text
THREAD NAME     	Stack usage
Control         	1048/2048
IDLE            	132/516
Default         	180/4304
Flush           	176/512
Tmr Svc         	148/516
Total HEAP      	51200
Current HEAP used	7776
Max HEAP used   	8144
ok
```

## 2. Selección de aplicación

Las tres aplicaciones son excluyentes entre sí. Al arrancar `INITF`/`RESPF`, el firmware imprime el volcado completo de parámetros de la sesión FiRa — útil para verificar la configuración efectiva.

### 2.1 `INITF` — FiRa TWR Initiator

Ayuda del firmware (confirma la sintaxis de todas las opciones, **incluidos los flags `-MULTI` y `-HOP` sin valor** y la lista de responders para uno-a-muchos):

```text
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
```

Respuesta real al arrancar (`INITF` sin opciones):

```text
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

Después del `ok` comienzan las notificaciones (ver §5).

### 2.2 `RESPF` — FiRa TWR Responder

Misma estructura que `INITF`; cambian el rol y las direcciones:

```text
FiRa Session Parameters: {
SESSION_ID: 42,
CHANNEL_NUMBER: 9,
DEVICE_ROLE: RESPONDER,
...
DEVICE_MAC_ADDRESS: 0x0001,
DST_MAC_ADDRESS: 0x0000
}
ok
```

### 2.3 `LISTENER` y `LSTAT`

`LISTENER` responde `ok` y, al iniciarse, informa las limitaciones del chip:

```text
Found non-AOA chip. PDoA is not available.
Listener Top Application: Started
```

Por **cada trama UWB recibida** emite un bloque JSON con el payload y los niveles de recepción (captura real, payload abreviado):

```text
JS010D{"LSTN":[49,2B,01,00,26,13,...,1D,09],"TS4ns":"0x0932C473","O":1299,"rsl":-80.96,"fsl":-91.94}
```

| Campo | Significado |
|---|---|
| `LSTN` | Payload de la trama en hex (máx. 127 bytes) |
| `TS4ns` | Timestamp de recepción (unidades de 4 ns) |
| `O` | Offset de frecuencia del cristal del transmisor |
| `rsl` | *RX Signal Level*: potencia **total** recibida [dBm] |
| `fsl` | *First path Signal Level*: potencia del **primer camino** [dBm] |

La relación `rsl − fsl` es el indicador de multipath — ver §5.4.

`LSTAT` (solo con LISTENER corriendo) reporta los contadores de eventos de RX:

```text
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

## 3. IDLE time commands (solo en modo NONE)

### 3.1 `UART`

Consulta del estado del UART por pines:

```text
UART: 0

ok
```

> **La placa tiene dos interfaces físicas de consola:** el adaptador USB integrado (USB CDC ACM — el que usa esta herramienta) y un **UART por pines** independiente, deshabilitado por defecto (guía §1.2). El comando `UART <DEC>` (`HELP UART`: *"To initialize selected UART: UART <DEC>"*) no agrega una segunda salida: **conmuta cuál de las dos interfaces recibe todas las respuestas del firmware**, `ok` incluido.
>
> **[Verificado 2026-08-10 contra el código fuente del SDK]** `flush_report_buf()` (`Src/Apps/Src/common/usb_uart/usb_uart_tx.c`) decide el destino de cada respuesta con un `if (is_uart_allowed()) { … UART … } else { … USB … }` — mutuamente excluyente, nunca ambas a la vez. Por eso **no se ejercita `UART 1` desde esta herramienta**: como el enlace con las placas es siempre por USB, tras `UART 1` el firmware seguiría ejecutando comandos con normalidad pero dejaría de responder **por completo** por USB (sin ningún byte, ni siquiera a nivel serie crudo) hasta que alguien enviara `UART 0` a través de los pines físicos — algo que esta herramienta no puede hacer. El síntoma sería indistinguible de una placa colgada.
>
> **[Verificado 2026-08-10 con hardware real, a pedido del usuario]** Se envió `UART 1` deliberadamente a una placa (sin `SAVE` posterior): efectivamente dejó de responder por USB, ni siquiera el eco del propio `ok` de confirmación (coherente con el mecanismo de arriba: el `ok` ya se enrutaba por los pines). Tras un **ciclo de alimentación**, la placa volvió a responder por USB con normalidad. Motivo, confirmado en `driver_app_config.c`: `comm_uart_allowed` vive en el mismo bloque de RAM de configuración que el resto de las claves (`ant_delay` incluida) — **sin `SAVE`, el cambio no persiste en NVM** y un reinicio lo revierte al valor de fábrica (`COMM_UART_ALLOWED_DEFAULT = false`, USB). Con `SAVE` de por medio, en cambio, el reinicio no alcanzaría para revertirlo: haría falta `UART 0` por los pines físicos.

### 3.2 `CALKEY`

Ayuda del firmware:

```text
CALKEY:
Set or get a calibration Key. Usage:
To set a value: "CALKEY <key> <value>"
To get a value: "CALKEY <key>"
```

**⚠ La forma de lectura está ROTA en fw 1.1.0** — responde `KO` para cualquier clave, incluidas las listadas por `LISTCAL`:

```text
CALKEY ant0.ch9.ant_delay

Please enter a valid key: ant0.ch9.ant_delay

KO
```

**La escritura funciona** y responde la línea `clave: valor` como confirmación. El valor de entrada se interpreta en **decimal**:

```text
CALKEY wifi_coex_time_gap 0
wifi_coex_time_gap: 0x00 (len: 1)

ok
```

*Workaround para leer una clave:* filtrar la salida de `LISTCAL` (así lo hace `calkey_read()` en esta herramienta).

### 3.3 `LISTCAL`

Reporta **259 claves** (el manual muestra solo un extracto). Estructura del espacio de nombres, con extracto real:

```text
restricted_channels: 0x0000 (len: 2)
wifi_coex_mode: 0x00 (len: 1)
wifi_coex_time_gap: 0x00 (len: 1)
ch5.wifi_coex_enabled: 0x01 (len: 1)
ch9.wifi_coex_enabled: 0x01 (len: 1)
...
xtal_trim: 0x19 (len: 1)
rf_noise_offset: 0xf9 (len: 1)
pdoa_lut0.data: 0x0000...0000 (len: 124)
...
ant0.ch5.ant_delay: 0x00003fed (len: 4)          ← retardo de antena, canal 5
ant0.ch5.pg_count: 0xaf (len: 1)
ant0.ch5.pg_delay: 0x34 (len: 1)
...
ant0.ch9.ant_delay: 0x00003ff7 (len: 4)          ← retardo de antena, canal 9
ant0.ch9.ref_frame0.tx_power_index: 0x19191919 (len: 4)
...
ant0.transceiver: 0x00 (len: 1)
ant0.port: 0x01 (len: 1)
...                                               (ant1, ant2, ant3: misma estructura)
ant_pair0.ch9.pdoa.offset: 0x0000 (len: 2)       ← offset de PDoA (calibración de ángulo)
...
ant_set0.rx_ants: 0xffff01 (len: 3)
ant_set0.tx_ant_path: 0x00 (len: 1)
...
experimental.mac.session_scheduler.id: 0x00 (len: 1)

ok
```

Observaciones:

- La clave `xtal_trim` **sí figura en LISTCAL** (`0x19`), pero `CALKEY xtal_trim` la rechaza — coherente con el bug de lectura.
- En esta placa de fábrica: `ant0.ch5.ant_delay = 0x3FED` (16365) y `ant0.ch9.ant_delay = 0x3FF7` (16375), coincidentes con los valores pregrabados en OTP (direcciones `0x01A` y `0x01C`).
- Existen 4 trayectos de antena (`ant0`–`ant3`) aunque el módulo tiene una sola antena; el trayecto activo es `ant0` (`ant_set0.tx_ant_path = 0x00`).

## 4. Service commands (solo en modo NONE)

### 4.1 `DIAG`

```text
DIAG            → 'DIAG: 0' + ok      (consulta)
DIAG 1          → ok                  (habilita; la consulta pasa a 'DIAG: 1')
DIAG 0          → ok                  (deshabilita)
```

### 4.2 `LCFG`

A diferencia de lo que sugiere el manual, responde un **bloque JSON** (`JS00A6`), solo aplicable a la aplicación LISTENER:

```text
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

### 4.3 `DECAID`

```text
Qorvo Device ID = 0xdeca0302
Qorvo Lot ID = 0x0000505634583230
Qorvo Part ID = 0x4ef24713
Qorvo SoC ID = 00005056345832304ef24713

ok
```

### 4.4 `SETAPP` y `SAVE`

Ambos responden `ok` sin más salida. `SETAPP` acepta `INITF`, `RESPF`, `LISTENER`, `NONE`; requiere `SAVE` posterior para persistir. `SAVE` no puede usarse durante una sesión de ranging (la propia ayuda del firmware lo advierte).

### 4.5 `GETOTP`

Bloque JSON con las 128 direcciones OTP (`0x000`–`0x07F`). Extracto real de la zona documentada (guía §1.4):

```text
OTP CONTENT: {
"0x000":"0x00000000",
...
"0x006":"0x4ef24713",      ← Part ID
"0x00d":"0x34583230",      ← Lot ID (parte baja)
"0x00e":"0x00005056",      ← Lot ID (parte alta)
"0x011":"0x79797979",      ← potencia TX FCC, canal 5
"0x013":"0x81818181",      ← potencia TX FCC, canal 9
"0x018":"0x00af00af",      ← PGCOUNT de producción
"0x01a":"0x3fed3fed",      ← retardos de antena canal 5 (16365)
"0x01c":"0x3ff73ff7",      ← retardos de antena canal 9 (16375)
"0x01e":"0x00be0019",      ← duración de trama | trimming del cristal
"0x01f":"0x00010201",      ← Platform ID | Cal Rev | OTP Revision
"0x020"–"0x034":            códigos de calibración de PLL (no nulos)
"0x035"–"0x07f":            0x00000000 (área libre de usuario sin grabar)
}
ok
```

### 4.6 `RESTORE` — ⚠ destructivo, no ejecutado

Solo se capturó su ayuda:

```text
RESTORE:
Restores the default configuration, both UWB and System.
```

> Pisa la configuración **y las claves de calibración** con los valores por defecto y los escribe automáticamente en NVM (guía §2.2). Esta herramienta nunca lo ejecuta sin confirmación explícita.

## 5. Notificaciones asincrónicas: acá se reporta la distancia

> **Punto clave:** ningún comando "consulta" la distancia. La distancia llega **sola, de forma asincrónica**, como notificaciones `SESSION_INFO_NTF` que ambas placas emiten cada `BLOCK` ms (200 por defecto) mientras corre una sesión TWR iniciada con `RESPF` (una placa) + `INITF` (la otra). Cesan con `STOP`.

### 5.1 `SESSION_STATUS_NTF` — estado de la sesión (no documentada en el manual)

Al arrancar una sesión:

```text
SESSION_STATUS_NTF: {state="INIT", reason="State change with session management commands"}
SESSION_STATUS_NTF: {state="IDLE", reason="State change with session management commands"}
SESSION_STATUS_NTF: {state="ACTIVE", reason="State change with session management commands"}
```

### 5.2 `SESSION_INFO_NTF` — la medición de distancia

Llega **en dos líneas** (la segunda arranca con un `\r` residual). Ejemplo real capturado en el banco a 2,20 m, con `DIAG 1` habilitado (por eso incluye RSSI):

```text
SESSION_INFO_NTF: {session_handle=1, sequence_number=0, block_index=0, n_measurements=1
\r [mac_address=0x0001, status="SUCCESS", distance[cm]=210, RSSI[dBm]=-78.0]}
```

| Campo | Significado |
|---|---|
| `session_handle` | Identificador de la sesión UWB |
| `sequence_number` / `block_index` | Contadores incrementales, uno por ronda de ranging |
| `n_measurements` | Cantidad de mediciones en la notificación (una por par; >1 en modo uno-a-muchos) |
| `mac_address` | Dirección del dispositivo par |
| `status` | Resultado de la ronda: `SUCCESS` o un error (p. ej. `RX_TIMEOUT`) |
| **`distance[cm]`** | **La distancia medida, entero en centímetros — solo presente con `status="SUCCESS"`** |
| `RSSI[dBm]` | Potencia de la señal recibida — solo presente con `DIAG 1` previo |

Cuando la ronda falla (par fuera de alcance, obstrucción, o `INITF` sin ningún `RESPF` activo), la notificación llega igual pero **sin distancia**:

```text
SESSION_INFO_NTF: {session_handle=1, sequence_number=3, block_index=3, n_measurements=1
\r [mac_address=0x0001, status="RX_TIMEOUT"]}
```

> **Nota práctica:** una muestra individual tiene resolución de 1 cm y dispersión de varios cm por multipath (en el banco de 2,20 m se observaron desvíos de 2–10 cm según el entorno). Para medir con precisión hay que **promediar** — esta herramienta usa 100 muestras (`collect_samples()`).

### 5.3 `RANGE_DIAGNOSTICS_NTF` — diagnóstico por trama (con `DIAG 1`, no documentada)

Con `DIAG 1`, además del RSSI, cada `SESSION_INFO_NTF` va seguida de una notificación de diagnóstico que detalla **las 6 tramas del intercambio DS-TWR** (multilínea, continuaciones con `\r`):

```text
RANGE_DIAGNOSTICS_NTF: {n_reports=6
\r [msg_id=CONTROL, action=TX, antenna_set=0, frame_status={SUCCESS: 1, WIFI_COEX: 0, GRANT_DURATION_EXCEEDED: 0}, cfo_present=0, nb_aoa=0];
\r [msg_id=RANGING_INITIATION, action=TX, antenna_set=0, frame_status={SUCCESS: 1, ...}, cfo_present=0, nb_aoa=0];
\r [msg_id=RANGING_RESPONSE, action=RX, antenna_set=0, frame_status={SUCCESS: 1, ...}, cfo_present=1, cfo_ppm=2.32, nb_aoa=0];
\r [msg_id=RANGING_FINAL, action=TX, antenna_set=0, frame_status={SUCCESS: 1, ...}, cfo_present=0, nb_aoa=0];
\r [msg_id=MEASUREMENT_REPORT, action=TX, antenna_set=0, frame_status={SUCCESS: 1, ...}, cfo_present=0, nb_aoa=0];
\r [msg_id=RESULT_REPORT, action=RX, antenna_set=0, frame_status={SUCCESS: 1, ...}, cfo_present=1, cfo_ppm=2.17, nb_aoa=0]}
```

Útil para diagnóstico fino: se ve qué trama del intercambio falló (en una ronda `RX_TIMEOUT`, el reporte se corta en la trama con `SUCCESS: 0`) y el offset de frecuencia del cristal del par (`cfo_ppm`, ~2,2–2,6 ppm en las capturas). Esta herramienta ignora estas notificaciones al medir.

### 5.4 Rebotes de señal (multipath / NLOS): qué detecta la tecnología y qué expone el CLI

> **Ver también:** [teoria-multipath-nlos.md](teoria-multipath-nlos.md) — desarrollo teórico completo (CIR, detección del primer camino, por qué el protocolo FiRa define un indicador de NLOS que Qorvo no implementa, y las técnicas de detección a nivel de host).

> **[Fuera del manual]** Fundamento: el receptor UWB estima la distancia a partir del tiempo de vuelo del **primer camino detectable** en la respuesta al impulso del canal (CIR). Por eso el ranging es robusto a los rebotes *mientras exista línea de vista*: los ecos llegan después y no corren la medición. El problema es el caso **NLOS** (camino directo bloqueado): el "primer camino" que detecta el chip es un rebote, y la distancia se **sobreestima siempre** (el camino reflejado es más largo). Un rebote nunca acorta la medición.

**Qué expone el firmware CLI 1.1.0:**

| Modo | Indicador disponible |
|---|---|
| Sesión TWR (`INITF`/`RESPF`) | **No hay indicador directo de NLOS** en `SESSION_INFO_NTF`. Heurísticas: saltos de distancia hacia arriba, caída del `RSSI` (con `DIAG 1`), aumento de la dispersión entre muestras y rondas `RX_TIMEOUT` intercaladas. |
| `LISTENER` (sniffer) | **Sí**: cada trama reporta `rsl` (potencia total) y `fsl` (potencia del primer camino). |

**Interpretación de `rsl − fsl`** **[Fuera del manual** — criterio de las notas de aplicación de Qorvo/Decawave (APS006, *DW3000 User Manual*)**]**: si el primer camino concentra la energía (diferencia chica, < ~6 dB), la señal es probablemente directa (LOS); si el primer camino llega debilitado respecto del total (diferencia grande, > ~10 dB), la energía dominante viene de rebotes y la medición es sospechosa de NLOS.

Ejemplo real (banco interior a 2,20 m, tramas consecutivas del mismo transmisor):

```text
"rsl":-80.42,"fsl":-82.98   → diferencia  2,6 dB  → primer camino fuerte (LOS)
"rsl":-80.96,"fsl":-91.94   → diferencia 11,0 dB  → primer camino débil
"rsl":-81.56,"fsl":-96.08   → diferencia 14,5 dB  → energía dominada por rebotes
```

La variación entre tramas consecutivas es normal en interiores (personas, muebles y paredes modifican el canal en tiempo real). Para juzgar un enlace conviene mirar la **tendencia** de muchas tramas, no una individual.

**¿Y por UCI? Tampoco hay veredicto NLOS — verificado contra el SDK del fabricante (QM33SDK-1.1.1):**

- El protocolo FiRa UCI **sí define un campo `nlos`** en cada medición TWR (*FiRa UCI Generic Technical Specification 2.0.0*, §RANGE_DATA_NTF; el parser del fabricante lo lee en `SDK/Tools/uwb-qorvo-tools/lib/uwb-uci/uci/qorvo_msg.py`, con el comentario *"Is a non-Line of sight measurement?"*).
- **Pero el stack de Qorvo no lo implementa en esta plataforma**: la propia herramienta `run_fira_twr` lo imprime fijo como `is nlos meas: Unsupported` (mismo archivo, método `__str__`), y todos los ejemplos de su README lo muestran así.
- Tampoco existe en `uwb-qorvo-tools` un script de volcado del CIR (los scripts disponibles cubren calibración, configuración, tests PER/CW y ranging). Las claves `rx_diag_config.cir_*` de §3.3 configuran el diagnóstico interno, pero ninguna interfaz de este SDK lo expone al usuario.

**Conclusión (verificada por CLI, por UCI y por el código del SDK):** en esta plataforma, ningún firmware entrega un indicador NLOS por medición. La determinación es responsabilidad del **host**, combinando los indicadores físicos disponibles (`rsl−fsl` en LISTENER, RSSI y dispersión en TWR) con algoritmos sobre la serie de mediciones: detección de saltos discretos, asimetría del error (el NLOS solo sobreestima) y, en un sistema de posicionamiento, redundancia geométrica entre anclas. Para el detalle del cálculo de `rsl`/`fsl`, ver las *Diagnostic APIs* del **DW3000 User Manual** (guía, Anexo C).

## 6. Tabla resumen

| Comando | Modo requerido | Termina en | Notas fw 1.1.0 |
|---|---|---|---|
| `HELP` / `HELP <CMD>` | cualquiera | `ok` | Ayuda disponible para todos los comandos |
| `STAT` | cualquiera | `ok` | Sin línea `MODE:`; JSON multilínea |
| `STOP` | cualquiera | `ok` | Tarda ~0,3 s en hacer efecto |
| `THREAD` | cualquiera | `ok` | Tabla de stacks y heap |
| `LISTENER` | NONE | `ok` | Chip no-AoA: sin PDoA; reporta `rsl`/`fsl` por trama (indicador de multipath, §5.4) |
| `LSTAT` | LISTENER activo | `ok` | JSON de eventos RX |
| `INITF` / `RESPF` | NONE | `ok` | Vuelca los parámetros FiRa; luego notificaciones |
| `UART` | NONE | `ok` | Solo consulta desde esta herramienta; la escritura conmuta USB↔pines (§3.1) — nunca ejecutada por USB |
| `CALKEY <key>` | NONE | **`KO`** | **Lectura rota** — usar LISTCAL |
| `CALKEY <key> <val>` | NONE | `ok` | Escritura OK; entrada **decimal**; responde el valor nuevo |
| `LISTCAL` | NONE | `ok` | 259 claves |
| `DIAG` / `DIAG 0\|1` | NONE | `ok` | Habilita RSSI y `RANGE_DIAGNOSTICS_NTF` en ranging |
| `LCFG` | NONE | `ok` | JSON; solo aplica a LISTENER |
| `DECAID` | NONE | `ok` | IDs del chip UWB |
| `SETAPP <app>` | NONE | `ok` | Requiere `SAVE` para persistir |
| `SAVE` | NONE (sin ranging) | `ok` | Persiste configuración y calibración en NVM |
| `GETOTP` | NONE | `ok` | JSON con 128 direcciones OTP |
| `RESTORE` | NONE | — | ⚠ Destructivo — no ejercitado |
