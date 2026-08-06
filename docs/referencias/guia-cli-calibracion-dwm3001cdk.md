# Guía Práctica de Uso y Calibración del Firmware CLI (DWM3001CDK)

> **Estado:** revisión verificada contra el manual del fabricante — agosto 2026
> **Alcance:** operación por consola CLI y calibración del retardo de antena (*antenna delay*) por TWR.

---

## 0. Documento de referencia

| Ítem | Detalle |
|---|---|
| Documento | *DWM3001CDK Developer Manual* |
| Release | QM33SDK-1.1.1 |
| Fecha | Agosto de 2025 (PDF generado el 13/08/2025) |
| Propietario | © 2025 Qorvo US, Inc. — *Qorvo Proprietary Information* |
| Extensión | 145 páginas numeradas |

Todas las referencias de sección y página de esta guía fueron **verificadas contra el PDF oficial**. Donde se agrega información que **no** proviene del manual, está marcada explícitamente con la etiqueta **[Fuera del manual]**.

---

## 1. Arquitectura y conceptos clave

### 1.1 Proyectos del SDK (cap. 6, pág. 35)

El SDK provee cuatro proyectos de ejemplo. Conviene ubicar el CLI dentro del conjunto:

| Proyecto | Función |
|---|---|
| **Hello World** | Interacción básica con el transceptor UWB; inicializa el stack, lee info del dispositivo y reporta por RTT. |
| **CLI** | Ejecuta aplicaciones FiRa TWR, sniffer UWB, información del dispositivo y **configuración y calibración**. |
| **UCI** | Control completo del stack FiRa desde un host externo por interfaz UCI; también permite configurar y calibrar. |
| **QANI** | *Qorvo Nearby Interaction*: interoperabilidad con el protocolo Nearby Interaction de Apple vía BLE + UWB. |

El firmware **CLI** ejecuta la aplicación de forma autónoma en el microcontrolador del módulo DWM3001C y expone una consola de comandos en texto plano. A diferencia del modo **UCI**, donde la placa es un módem controlado por un procesador externo, en CLI la lógica de control UWB corre en la propia placa.

> **[Fuera del manual]** El microcontrolador del módulo DWM3001C es un Nordic **nRF52833** (dato del *DWM3001C Data Sheet*, no del Developer Manual). Los ejemplos de salida de `STAT` y `HELP` que aparecen en el manual fueron capturados sobre una placa **nRF52840DK**, por eso muestran ese nombre; en un DWM3001CDK el campo `Device` reporta la placa correspondiente.

### 1.2 Interfaces de comunicación (secc. 4.1 y 4.1.1, págs. 11–12)

La placa tiene **dos** conectores micro-USB:

| Conector | Función |
|---|---|
| **J9** (*interface MCU*) | Flasheo y depuración vía J-Link OB, **y** comunicación UART con el MCU a través de un puerto COM virtual. |
| **J20** (*nRF USB*) | Comunicación UART/USB directa con el MCU. |

En el SDK para DWM3001CDK existen **dos interfaces de comunicación**:

- **UART sobre USB (USB CDC ACM)** — **habilitada por defecto**.
- **UART por pines** — **deshabilitada por defecto**.

> **Corrección importante.** El UART por pines **no requiere recompilar el firmware**: se habilita desde la propia consola con el comando `UART 1` (secc. 7.5.2, pág. 46). La única excepción es que el comando `UART` **no está disponible si se removió el flag de compilación `USB_ENABLE`** del proyecto.

**Parámetros del terminal serie** (secc. 7.2, pág. 37):

| Parámetro | Valor |
|---|---|
| Baud rate | 115200 |
| Bits de datos | 8 |
| Paridad | Ninguna |
| Bits de stop | 1 |
| Control de flujo | Ninguno |

> El manual sugiere Tera Term y, para usar las teclas del teclado numérico, cargar el mapa de teclas `FUNCTION.CNF` desde *Setup → Load Key Map*.

### 1.3 Estados de operación: NONE / IDLE

Este punto es clave para entender qué comando se puede ejecutar y cuándo.

- `STOP` detiene cualquier aplicación de nivel superior y deja el dispositivo en **modo NONE**, donde solo corren las tareas de núcleo (secc. 7.3.4, pág. 41).
- Los **Service Commands** (secc. 7.4) solo pueden ejecutarse en **modo NONE**, sin aplicaciones corriendo.
- Los **IDLE time Commands** (secc. 7.5) solo pueden ejecutarse cuando el modo es **NONE**, que es lo que el manual llama *estado IDLE*.

> **En resumen: NONE e IDLE son el mismo estado.** El manual usa "IDLE time commands" como nombre de la categoría y "NONE" como nombre del modo que reporta `STAT`. `CALKEY`, `LISTCAL`, `UART`, `SAVE`, `RESTORE`, `SETAPP`, `GETOTP`, `DIAG`, `LCFG` y `DECAID` **requieren todos** estar en NONE.

Las aplicaciones de nivel superior (`INITF`, `RESPF`, `LISTENER`) **no pueden correr simultáneamente**, porque comparten los mismos recursos (secc. 7.6.1, pág. 48).

Las aplicaciones y comandos de configuración devuelven `ok` cuando se ejecutaron correctamente.

### 1.4 Persistencia: RAM, NVM y OTP

**Mecanismo de NVM** (secc. 12.3.1, pág. 88):

- Todas las variables que deben almacenarse en NVM residen en una sección de RAM consecutiva.
- Esa sección se copia como un bloque único dentro de la NVM.
- Al arrancar, la configuración se carga desde NVM y se copia a esa sección de RAM.
- `SAVE` escribe el bloque de configuración de RAM en Flash.
- `RESTORE` restaura la configuración por defecto junto con **los valores de calibración por defecto**.

**Nota del fabricante** (secc. 14.1.1.1, pág. 131): la configuración y la calibración son **no volátiles**; se conservan tras un ciclo de alimentación o una actualización de firmware, **siempre que no se borre la NVM**.

**Memoria OTP** (cap. 15, págs. 138–140): es memoria de fábrica dentro del chip UWB, de una sola grabación, leída con `GETOTP`. Contiene, entre otros:

| Dirección | Contenido |
|---|---|
| `0x011` | Valor de potencia TX compatible con FCC, canal 5 |
| `0x013` | Valor de potencia TX compatible con FCC, canal 9 |
| `0x018` | PGCOUNT usado en los tests de producción |
| **`0x01A`** | **Retardos de antena para canal 5** (STS segment length = 64) |
| **`0x01C`** | **Retardos de antena para canal 9** (STS segment length = 64) |
| `0x01E` | Duración de trama en tests de producción \| trimming del cristal |
| `0x01F` | Platform ID \| Cal Rev \| OTP Revision |
| `0x036–0x05F`, `0x062–0x077` | Libres para el usuario |
| `0x078–0x07F` | Recomendado para clave AES |

> **Advertencia del manual (pág. 140).** Los retardos de antena pregrabados en OTP están calibrados **para las antenas específicas provistas con el kit de desarrollo**. Si el diseño usa otra antena, los retardos deben recalibrarse individualmente.

---

## 2. Referencia de comandos CLI (cap. 7)

### 2.1 Anytime Commands (secc. 7.3, págs. 38–41)

Ejecutables en cualquier momento, incluso con una sesión de ranging activa.

| Comando | Secc. | Descripción |
|---|---|---|
| `HELP` / `?` | 7.3.2 | Lista los comandos disponibles en el modo actual, o muestra la ayuda de `<CMD>` si se le pasa un comando como parámetro (ej. `HELP INITF`). |
| `STAT` | 7.3.3 | Reporta estado: versión de software, aplicaciones soportadas y modo de operación actual. |
| `STOP` | 7.3.4 | Detiene la aplicación en curso y pasa a modo **NONE**. |
| `THREAD` | 7.3.5 | Información de hilos y consumo de memoria (profundidad máxima alcanzada / tamaño asignado). |

Salida típica de `STAT`:

```text
MODE: NONE
JS0108{"Info":{
"Device":"...",
"Current App":"NONE",
"Version":"1.0.0",
"Build":"...",
"Apps":["LISTENER","RESPF","INITF"],
"Driver":"DW3XXX Device Driver Version 08.00.26",
"UWB stack":"R12.7.0-..."}}
```

> **[Nota 2026-08-06 — verificado con hardware real]** En una DWM3001CDK con firmware CLI 1.1.0 (build 13/08/2025, `Device: "DWM3001CDK - DW3_QM33_SDK - FreeRTOS"`), la salida real de `STAT` difiere del ejemplo del manual en tres puntos: **(1)** no se emite la línea `MODE: NONE` — el modo debe derivarse del campo `"Current App"` del JSON; **(2)** el bloque `JSxxxx{...}` llega **partido en varias líneas** (una por campo); **(3)** la placa hace **eco del comando** como primera línea y cierra la respuesta con una línea `ok`. El terminador de línea aceptado en TX es `\r\n`.

### 2.2 Service Commands (secc. 7.4, págs. 41–46) — solo en modo NONE

| Comando | Secc. | Descripción |
|---|---|---|
| `RESTORE` | 7.4.1 | Restaura la configuración por defecto de las aplicaciones CLI **y de las claves de calibración L1**. Los parámetros restaurados **se guardan y escriben automáticamente en NVM**. |
| `LCFG` | 7.4.2 | Consulta/modifica parámetros de configuración — **solo de la aplicación LISTENER**. |
| `DIAG` | 7.4.3 | Habilita el modo diagnóstico (`DIAG 1`) o lo consulta (`DIAG`). Por defecto 0. |
| `DECAID` | 7.4.4 | Reporta información del chip: Device ID, Lot ID, Part ID y SoC ID. |
| `SAVE` | 7.4.5 | Guarda los parámetros de configuración de las aplicaciones CLI en NVM. **No puede usarse durante una sesión de ranging.** |
| `SETAPP` | 7.4.6 | Define la aplicación por defecto tras reboot o ciclo de alimentación. Requiere `SAVE` posterior para persistir. |
| `GETOTP` | 7.4.7 | Vuelca el contenido de la memoria OTP. |

> **Corrección respecto de la versión anterior de esta guía:** `LCFG` **no** es un comando de configuración general del sistema. El manual es explícito: *"This command only concerns LISTENER application"*. Sus parámetros (`CHAN`, `PAC`, `PCODE`, `SFDTYPE`, `DRATE`, `PHRMODE`, `PHRRATE`, `STSMODE`, `STSLEN`, `PDOAMODE`, `XTALTRIM`) aplican al sniffer, no a las sesiones FiRa.

**`DECAID`** también estaba ausente de la versión anterior. Salida:

```text
Qorvo Device ID = 0xdeca0304
Qorvo Lot ID    = 0x0000503639463438
Qorvo Part ID   = 0x8124d5b7
Qorvo SoC ID    = 00005036394634388124d5b7
```

**`SETAPP`** acepta `LISTENER`, `INITF`, `RESPF` o `NONE` (sin aplicación por defecto, arranca en IDLE).

### 2.3 IDLE time Commands (secc. 7.5, págs. 46–47) — solo en modo NONE

| Comando | Secc. | Descripción |
|---|---|---|
| `UART` | 7.5.2 | Reporta o cambia el estado del UART por pines. `UART` consulta; `UART 0` deshabilita; `UART 1` habilita. |
| `CALKEY` | 7.5.3 | Lee o escribe una clave de calibración. |
| `LISTCAL` | 7.5.4 | Lista **todas** las claves de calibración disponibles con sus valores. |

**Sintaxis de `CALKEY`:**

```text
CALKEY <KEY>              → lee el valor de la clave
CALKEY <KEY> <VALUE>      → escribe el valor de la clave
```

Ejemplo textual del manual:

```text
CALKEY xtal_trim
xtal_trim: 0x32 (len: 1)

CALKEY xtal_trim 1
xtal_trim: 0x1 (len: 1)
```

> **Formato de los valores.** El firmware **responde en hexadecimal** e indica la longitud del campo en bytes (`len`). En el ejemplo del manual el valor se ingresa en decimal (`1`) y se devuelve como `0x1`. Conviene confirmar el formato de entrada con una lectura previa antes de escribir un valor grande.

**Salida de `LISTCAL`** (extracto textual del manual):

```text
restricted_channels: 0x0000 (len: 2)
wifi_coex_mode: 0x00 (len: 1)
wifi_coex_time_gap: 0x00 (len: 1)
ch5.wifi_coex_enabled: 0x01 (len: 1)
ch5.pll_locking_code: 0x00 (len: 1)
ch9.wifi_coex_enabled: 0x01 (len: 1)
...
ant_set0.rx_ants: 0x000001 (len: 3)
ant_set0.tx_ant_path: 0x00 (len: 1)
ant_set0.nb_rx_ants: 0x01 (len: 1)
ant_set0.rx_ants_are_pairs: 0x00 (len: 1)
ant_set0.tx_power_control: 0x01 (len: 1)
experimental.mac.session_scheduler.id: 0x00 (len: 1)
```

Obsérvese la estructura del espacio de nombres: claves globales, claves por canal (`ch5.`, `ch9.`), claves por trayecto de antena (`ant<x>.`) y claves por conjunto de antenas (`ant_set<x>.`). **`ant<x>` y `ant_set<x>` son cosas distintas** y no deben confundirse.

### 2.4 Application Commands (secc. 7.6, págs. 48–54)

| Comando | Descripción |
|---|---|
| `INITF` | Configura la placa como **INITIATOR** en una sesión FiRa TWR. |
| `RESPF` | Configura la placa como **RESPONDER** en una sesión FiRa TWR. |
| `LISTENER` | Modo receptor: reporta todos los paquetes recibidos (sniffer PHY). `LSTAT` muestra estadísticas mientras corre. |

> **Advertencia del manual (secc. 7.2, pág. 38 y 7.6.2.1, pág. 48).** Antes de iniciar aplicaciones FiRa hay que revisar las *Calibration important notes*. Usar la calibración por defecto **degrada la exactitud del ranging**.

**Parámetros de `INITF` / `RESPF`** (tabla 7.6, págs. 49–50):

| Opción | Default | Rango | Descripción |
|---|---|---|---|
| `CHAN` | 9 | 5 o 9 | Número de canal |
| `PRFSET` | BPRF4 | BPRF3…BPRF6 | Conjunto de parámetros de PRF |
| `PCODE` | 10 | 9 a 12 | Índice de código de preámbulo |
| `SLOT` | 2400 | 2400 a 65535 | Duración del slot en RSTU (1 ms = 1200 RSTU) |
| `BLOCK` | 200 | 1 a 65535 | Duración del bloque de ranging en ms |
| `ROUND` | 25 | 1 a 255 | Cantidad de slots dentro de una ronda |
| `RRU` | DSTWR | SSTWR, DSTWR, SSTWRNDEF, DSTWRNDEF | Uso de la ronda de ranging |
| `ID` | 42 | 1 a 65535 | Session ID |
| `VUPPER` | 01:02:…:08 | 00:…:00 a FF:…:FF | vUpper64 (parte estática del STS) |
| `MULTI` | desactivado | — | Modo uno-a-muchos |
| `HOP` | desactivado | — | *Round hopping* |
| `ADDR` | Initiator 0 / Responder 1 | 0 a 65535 | Dirección propia |
| `PADDR` | Initiator 1 / Responder 0 | 0 a 65535 | Dirección(es) del par |

**Sintaxis:** las opciones llevan **guion delante** y son todas opcionales, admiten cualquier orden y **las mayúsculas no son obligatorias**.

```text
INITF
INITF -CHAN=9 -PRFSET=BPRF4 -SLOT=2400 -BLOCK=200 -ROUND=25 -RRU=DSTWR -ID=42 -VUPPER=01:02:03:04:05:06:07:08 -ADDR=0 -PADDR=1
```

> **Restricciones de combinación (nota del manual, pág. 50):** `ROUND` debe ajustarse al `RRU` y a la cantidad de *controlees*; el tiempo de `SLOT` multiplicado por la cantidad de slots de una ronda **no puede exceder** el tiempo de `BLOCK`; y no se recomienda superar 17 s de `BLOCK`.

**Persistencia de parámetros de sesión** (listings 7.7 a 7.10, págs. 50–51). Este comportamiento es sutil y conviene tenerlo presente:

```text
RESPF -CHAN=5 -BLOCK=400
STOP
SAVE
RESPF                      → corre con CHAN=5 y BLOCK=400 (guardados)
```

Pero:

```text
RESPF -CHAN=5 -BLOCK=400
STOP
SAVE
RESPF -PRFSET=BPRF3        → corre con PRFSET=BPRF3, pero CHAN=9 y BLOCK=200 (¡defaults!)
```

> **Regla:** el próximo comando de aplicación que reciba **cualquier** parámetro **resetea todos los demás a su valor por defecto** y aplica solamente el provisto. Para volver a los defaults, ejecutar `INITF`/`RESPF` con al menos un parámetro por defecto, o usar `RESTORE`.

**Formato de salida durante el ranging** (secc. 7.6.2.2, págs. 51–52):

```text
SESSION_INFO_NTF: {session_handle=1, sequence_number=0, block_index=0, n_measurements=1
  [mac_address=0x0001, status="SUCCESS", distance[cm]=91, loc_az_pdoa=65.35, loc_az=24.90,
   loc_el_pdoa=32.12, loc_el=12.01, rmt_az=22.84, rmt_el=13.59, RSSI[dBm]=-66.5]}
```

| Campo | Significado |
|---|---|
| `session_handle` | Identificador único de la sesión UWB |
| `sequence_number` | Contador desde 0, se incrementa en cada notificación |
| `block_index` | Índice del bloque actual |
| `n_measurements` | Cantidad de medidas en la notificación (una por par iniciador/respondedor) |
| `mac_address` | Dirección MAC del dispositivo par |
| `status` | Estado de la ronda de ranging |
| **`distance[cm]`** | **Distancia entre dispositivos, en centímetros** |
| `loc_az_pdoa` / `loc_el_pdoa` | PDoA local crudo, azimut / elevación [-180…+180] |
| `loc_az` / `loc_el` | AoA local, azimut / elevación [-90…+90] |
| `rmt_az` / `rmt_el` | AoA remoto, azimut / elevación [-90…+90] |
| `RSSI` | Solo se muestra con `DIAG` habilitado |

> **Consecuencia práctica para calibrar:** la distancia se reporta **como entero en centímetros**. La resolución de una muestra individual es de 1 cm, así que para resolver por debajo de ese valor hay que promediar muchas muestras. Y para ver el RSSI —útil para juzgar la calidad del enlace— hay que ejecutar `DIAG 1` **antes** de arrancar el ranging.

---

## 3. Calibración: conceptos (cap. 14, págs. 131–137)

### 3.1 Qué abarca la calibración (secc. 14.1.1, pág. 131)

El sistema de calibración y configuración es un mecanismo **clave/valor** que define:

- El conjunto de antenas usado para transmitir y recibir tramas.
- El conjunto de antenas y el segmento de trama usados para calcular AoA.
- La tabla de correspondencia entre PDoA medido y AoA para la antena montada.
- La potencia de TX para las distintas fases de transmisión, **el retardo de antena**, etc.

La calibración puede aplicarse por **UCI** (archivo JSON, script `load_cal`), por **GUI** o manualmente por **CLI** con `CALKEY`.

### 3.2 Claves relevantes (secc. 14.5, págs. 135–137)

| Magnitud | Clave | Notas |
|---|---|---|
| **Retardo de antena (distancia)** | `ant<x>.ch<y>.ant_delay` | `x` = trayecto de antena usado; `y` = canal usado |
| **Offset de PDoA (ángulo)** | `ant_pair<x>.ch<y>.pdoa.offset` | `x` = par de antenas; `y` = canal |

> **Este es el dato que faltaba en la versión anterior de la guía.** Con la configuración por defecto de un DWM3001CDK (canal 9, un solo trayecto de antena), la clave a tocar es **`ant0.ch9.ant_delay`**. Si se trabaja en canal 5, es `ant0.ch5.ant_delay`. **La calibración es por canal**: calibrar en 9 no calibra el 5.

El manual indica que esta calibración **se almacena en NVM, por lo que se realiza una sola vez**.

Para el diccionario completo de claves L1, el manual remite al documento **`uwb-l1-configuration`** (secc. 14.3, pág. 134), que es una publicación aparte del propio Developer Manual.

### 3.3 Cuándo hay que calibrar sí o sí (secc. 14.1.1.1, pág. 131)

El dispositivo queda con la **calibración por defecto** —y por lo tanto con exactitud degradada— en cualquiera de estos casos:

1. Cuando se recibe una placa nueva del kit de desarrollo.
2. Cuando se borra toda la FLASH / NVM.
3. Cuando se usa el comando **`RESTORE`**.
4. Cuando se usa el script `reset_calibration` de Python.

> **Advertencias adicionales del manual:**
> - Si el hardware **no** es un QM33120WDK1, DWM3001CDK o Type2AB EVB (por ejemplo, un DWM3000EVB), **hay que flashear la calibración antes de iniciar cualquier sesión de ranging**.
> - **La compatibilidad de los datos de calibración entre versiones del SDK no está garantizada**, y el ranging puede llegar a no funcionar en absoluto. Al actualizar el SDK, recalibrar.
> - Para tests de producción y calibración de producto final, el manual remite a la nota de aplicación **APS312 — *Production Tests for DW3000-Based Products***. El procedimiento de esta guía es para **evaluación**, no para producción.

### 3.4 Borrado de la calibración (secc. 14.4, pág. 134)

- **Resetear** a la calibración por defecto: script `reset_calibration.py`, en `SDK/Tools/uwb-qorvo-tools/scripts/generic/device/reset_calibration`.
- **Borrar completamente**: los pasos son los mismos que los de flasheo, pero usando el botón **"Erase chip"** en lugar de programar, y luego reflashear el firmware.

---

## 4. Procedimiento de calibración de distancia por CLI

### 4.1 El procedimiento oficial (secc. 14.5.1.1, pág. 136)

El manual lo enuncia en seis pasos:

1. Colocar los dos dispositivos a una **distancia conocida**. Se recomienda **aproximadamente 2 metros**.
2. Iniciar una sesión de Two-Way Ranging con `INITF` y `RESPF`.
3. **Detener el ranging en el dispositivo que se quiere calibrar.**
4. Ajustar su retardo de antena con `CALKEY`.
5. Reiniciar la sesión de TWR.
6. Repetir los últimos tres pasos **hasta que la distancia reportada coincida con la distancia real medida**.

> **Corrección importante respecto de la versión anterior de esta guía.** El método oficial es **iterativo y sobre un solo dispositivo a la vez**: se calibra el equipo bajo prueba contra el otro, que actúa de referencia. No hay en el manual una fórmula cerrada para calcular el nuevo valor de retardo, ni una instrucción de repartir la corrección entre ambas placas.
>
> **También:** la distancia recomendada por el fabricante es **2 m**, no 3–10 m. Y el procedimiento oficial **no incluye `SAVE`** — ver 4.3.

### 4.2 Procedimiento detallado

```text
[Placa A: Initiator]  <---- distancia real conocida (~2,00 m) ---->  [Placa B: Responder]
        |                                                                    |
   Ejecuta: INITF                                                     Ejecuta: RESPF
```

**Paso 1 — Montaje**

- Dos placas fijas, alineadas y en visión directa (LOS).
- **[Fuera del manual]** El módulo DWM3001C tiene polarización vertical según su hoja de datos, así que conviene mantener ambas placas verticales y enfrentadas, elevadas del piso y alejadas de superficies metálicas.
- Medir la distancia real entre antenas con la mayor precisión posible.
- Anotar el **canal** en uso; será el `<y>` de la clave.

**Paso 2 — Estado inicial**

En la placa a calibrar, en modo NONE:

```text
STAT
LISTCAL
```

Registrar el valor actual de `ant<x>.ch<y>.ant_delay` antes de modificar nada. Opcionalmente:

```text
DIAG 1
GETOTP
DECAID
```

**Paso 3 — Sesión de medición**

```text
RESPF          (en la placa B)
INITF          (en la placa A)
```

Observar el campo `distance[cm]` de las notificaciones `SESSION_INFO_NTF`.

**Paso 4 — Promediar**

- **[Fuera del manual]** El manual no fija un tamaño de muestra para CLI. Dado que la distancia se reporta como entero en centímetros, conviene promediar **al menos 100 muestras** y registrar también la dispersión. Con `BLOCK=200` ms se obtiene una medición cada ~200 ms, es decir unos 20 s para 100 muestras.
- Calcular $\Delta D = D_{medida} - D_{real}$.
- Para el flujo UCI, el manual sí ofrece una ayuda: el script `run_fira_twr` con el parámetro `-stat` entrega la **distancia promedio** al finalizar. No existe un equivalente en CLI, así que hay que capturar el log del terminal y promediar aparte.

**Paso 5 — Ajuste**

Detener el ranging **en el dispositivo a calibrar** y escribir la nueva clave:

```text
STOP
CALKEY ant0.ch9.ant_delay              ← leer el valor actual
CALKEY ant0.ch9.ant_delay <nuevo>      ← escribir el valor corregido
```

**Sentido del ajuste:** aumentar el retardo de antena **reduce** la distancia reportada. Si la placa mide de más, el retardo sube.

> **[Fuera del manual] — Cómo estimar el salto.** El manual no da ninguna equivalencia entre unidades de retardo y milímetros; propone puro tanteo iterativo. Como referencia externa, en la familia DW3000 una unidad de retardo equivale a ~15,65 ps, es decir **~4,7 mm de distancia**. Pero el factor efectivo depende de cómo el firmware aplique la clave a TX y RX, así que **el método robusto es medirlo**: en la primera iteración, cambiar la clave en un valor conocido $N$, volver a medir, y calcular la sensibilidad real en mm/unidad. Con eso, la segunda iteración suele cerrar el ajuste.

**Paso 6 — Verificar e iterar**

```text
RESPF / INITF          ← reiniciar la sesión
```

Repetir los pasos 4 a 6 hasta que la distancia reportada coincida con la real.

### 4.3 Sobre el guardado: `SAVE` y calibración

Este punto merece una aclaración porque el manual **no es del todo explícito** y la versión anterior de esta guía lo afirmaba de más:

- El manual dice que la calibración **"se almacena en NVM, por lo que puede realizarse una sola vez"** (secc. 14.5.1) y que la configuración y calibración son **no volátiles**, retenidas tras un ciclo de alimentación (secc. 14.1.1.1).
- El procedimiento oficial de calibración por CLI **no menciona `SAVE`** en ninguno de sus seis pasos.
- Por otro lado, `SAVE` está descripto como el comando que guarda **"los parámetros de configuración de las aplicaciones CLI"** en NVM (secc. 7.4.5), y la secc. 12.3.1 dice que `SAVE` escribe el bloque de configuración de RAM en Flash.
- `RESTORE`, en cambio, sí declara explícitamente que **los parámetros restaurados se guardan y escriben automáticamente en NVM** — incluidas las claves de calibración L1.

**Recomendación práctica:** ejecutar `SAVE` en modo NONE al terminar (es inocuo) y luego **verificar empíricamente**: quitar la alimentación, reconectar y ejecutar `LISTCAL`. Si el valor persiste, la calibración quedó en NVM. Esa verificación de un minuto elimina la ambigüedad para tu SDK y tu placa concretos.

```text
STOP
SAVE
[ciclo de alimentación]
LISTCAL          ← confirmar que ant<x>.ch<y>.ant_delay conservó el valor
```

---

## 5. Alternativas al procedimiento manual

### 5.1 Calibración automática por GUI (secc. 8.2.8, págs. 71–75)

La **Qorvo One TWR GUI** puede calibrar la distancia automáticamente:

- El dispositivo a calibrar se coloca a **2 metros** del dispositivo que oficia de **calibrador** (el par).
- **El responder siempre usa el iniciador como calibrador.** El iniciador puede elegir como calibrador a cualquiera de los responders conectados **localmente** a la aplicación.
- Un dispositivo conectado a un host remoto **no puede** usarse para calibrar.
- La duración del procedimiento depende de la duración de ronda de la configuración FiRa.
- El botón indica el estado; cerrar el popup detiene la calibración en curso. Puede fallar por timeout o por error de comunicación.
- Los dispositivos ya calibrados quedan marcados con un tilde; los que no soportan calibración tienen el botón deshabilitado.

La GUI además permite **exportar** la calibración actual a JSON, editarla e **importarla** (secc. 14.2.2, págs. 133–134).

### 5.2 Calibración por UCI (secc. 14.2.1 y 14.5.1.2, págs. 133 y 136)

- El paquete `uwb-qorvo-tools`, en `SDK/Tools/uwb-qorvo-tools`, provee el script **`load_cal`** para cargar valores de calibración desde un archivo JSON al dispositivo vía UCI.
- Para el bucle de calibración: `run_fira_twr` en ambos dispositivos (con `-stat` para obtener la distancia promedio al final), luego **`set_cal`** en el dispositivo a calibrar, y repetir.
- La documentación de estos scripts está en `SDK/Tools/uwb-qorvo-tools/UWB-Qorvo-Tools-guide.pdf`.

> Para el proyecto de posicionamiento con Raspberry Pi, esta es la vía más automatizable: `run_fira_twr -stat` resuelve el promediado que en CLI hay que hacer a mano.

---

## 6. Resumen de flujo de comandos

```text
[Encendido]
     |
     v
   STAT                      Confirmar MODE: NONE
     |
     v
  LISTCAL                    Registrar ant<x>.ch<y>.ant_delay actual
     |
     v
  DIAG 1                     (opcional) habilitar RSSI en las notificaciones
     |
     v
  RESPF   (placa B)          Iniciar TWR a distancia conocida (~2 m)
  INITF   (placa A)
     |
     v
[Promediar distance[cm]]     dD = D_medida - D_real
     |
     v
   STOP                      <-- OBLIGATORIO: CALKEY solo corre en modo NONE
     |
     v
  CALKEY ant<x>.ch<y>.ant_delay <valor>
     |
     v
  LISTCAL                    Verificar que quedó escrito
     |
     v
[Reiniciar TWR y medir]  ----+
     |                       |
     |  ¿coincide?  NO ------+   (iterar: STOP -> CALKEY -> medir)
     |
    SI
     v
   STOP  ->  SAVE            Consolidar
     |
     v
[Ciclo de alimentación]
     |
     v
  LISTCAL                    Confirmar persistencia en NVM
```

---

## Anexo A — Correcciones aplicadas tras el contraste con el manual oficial

Errores y omisiones de la **versión anterior de esta guía**, ya corregidos arriba:

| # | Afirmación anterior | Qué dice el manual |
|---|---|---|
| 1 | Habilitar el UART por pines requiere modificar el firmware y recompilar. | **Falso.** Existe el comando `UART 1` (secc. 7.5.2). Solo deja de estar disponible si se removió el flag `USB_ENABLE`. |
| 2 | Los nombres de las claves de calibración no están documentados; hay que deducirlos de `LISTCAL`. | **Están documentados.** `ant<x>.ch<y>.ant_delay` para distancia y `ant_pair<x>.ch<y>.pdoa.offset` para ángulo (secc. 14.5). |
| 3 | `SAVE` es imprescindible para persistir lo escrito con `CALKEY`. | **No confirmado.** El procedimiento oficial no incluye `SAVE` y declara que la calibración ya se almacena en NVM. Ver la discusión en 4.3. |
| 4 | Distancia de trabajo recomendada: 3 a 10 m. | El manual recomienda **~2 metros**, tanto para CLI como para la GUI. |
| 5 | Se debe repartir la corrección entre ambas placas, con fórmula de $(\varepsilon_A + \varepsilon_B)/2$. | El método oficial calibra **un dispositivo por vez** contra el otro como referencia, de forma iterativa. La fórmula no aparece en el manual. |
| 6 | El valor por defecto del retardo de antena de canal 5 es 16390. | Ese dato es de la QSG/datasheet, **no** del Developer Manual. Además, el canal **por defecto del SDK es el 9**, no el 5. |
| 7 | `LCFG` es un comando de configuración general. | **Solo concierne a la aplicación LISTENER** (secc. 7.4.2). |
| 8 | El estado tras `STOP` se llama IDLE. | El modo se llama **NONE**; "IDLE time commands" es el nombre de la categoría. Son el mismo estado, pero `STAT` reporta `MODE: NONE`. |
| 9 | `DECAID` no figuraba. | Existe como Service Command (secc. 7.4.4). |
| 10 | Sintaxis de opciones sin guion (`CHAN=9`). | El manual usa **`-CHAN=9`**, con guion. |
| 11 | No se mencionaba el comportamiento de reseteo de parámetros. | Cualquier parámetro provisto en un comando de aplicación **resetea el resto a sus defaults** (listings 7.7–7.10). |
| 12 | No se mencionaba el formato de la salida de ranging. | `distance[cm]` es **entero en centímetros**; el RSSI requiere `DIAG 1`. |
| 13 | No se mencionaba la incompatibilidad entre versiones de SDK. | El manual advierte que la calibración **puede no ser compatible** entre versiones y que el ranging podría no funcionar. |

## Anexo B — Información de esta guía que no proviene del Developer Manual

Marcada en el cuerpo con **[Fuera del manual]**, se lista acá para trazabilidad:

- El microcontrolador del DWM3001C es un nRF52833 (fuente: *DWM3001C Data Sheet*).
- La polarización vertical de la antena del módulo y las recomendaciones de montaje (altura, alejamiento de metales).
- La equivalencia ~15,65 ps ≈ ~4,7 mm por unidad de retardo (familia DW3000). **El manual no la menciona**; se incluye solo como orden de magnitud y con la recomendación de medir la sensibilidad real de forma empírica.
- El tamaño de muestra sugerido (≥100 mediciones) y el criterio de dispersión.
- El valor por defecto 16390 para el retardo de antena de canal 5 (fuente: *DWM3001CDK Quick Start Guide*), aplicable a muestras de ingeniería.

## Anexo C — Documentos complementarios citados por el manual

| Documento | Para qué |
|---|---|
| `uwb-l1-configuration` | Diccionario completo de claves L1 de calibración y configuración (secc. 14.3) |
| `SDK/Tools/uwb-qorvo-tools/UWB-Qorvo-Tools-guide.pdf` | Scripts `load_cal`, `set_cal`, `run_fira_twr`, `reset_calibration` |
| **APS312** — *Production Tests for DW3000-Based Products* | Calibración de producto final y tests de producción |
| APS304 — *Increasing Range Using an External LNA* | Diseños con LNA externo |
| *DW3000 User Manual*, secciones de Diagnostic APIs | Detalle del cálculo de RSSI |
