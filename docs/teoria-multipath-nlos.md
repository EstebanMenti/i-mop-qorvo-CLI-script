# Multipath y NLOS en UWB: fundamento teórico y su implementación en el DWM3001C

> **Propósito:** explicar, a nivel de principios físicos y de protocolo, cómo el ranging UWB tolera el multipath, por qué falla en condiciones NLOS (*Non-Line-Of-Sight*), qué prevé el estándar FiRa al respecto, y qué de eso está realmente implementado en el módulo Qorvo DWM3001C — tanto en el firmware **CLI** (el que usa este proyecto) como en el firmware **UCI** (la interfaz binaria que corre el mismo stack UWB, ver §3).
> **Alcance:** documento teórico, complementario a [referencia-comandos-fw110.md](referencia-comandos-fw110.md) (que documenta las salidas reales del firmware CLI) y a la [guía de referencia](referencias/guia-cli-calibracion-dwm3001cdk.md) (comportamiento verificado contra el Developer Manual). Todo lo que no proviene de documentación oficial de Qorvo/FiRa se marca **[Fuera del manual]**, según la convención del proyecto.
> **Origen:** consolida una serie de preguntas y verificaciones realizadas el 2026-08-06, incluyendo capturas reales del firmware CLI y una revisión completa del **código fuente y la documentación del SDK del fabricante** (QM33SDK-1.1.1: firmware fuente en `SDK/Firmware/`, documentación oficial en `SDK/Documentation/`, herramientas Python en `SDK/Tools/`).

---

## 1. Cómo mide distancia el ranging UWB

### 1.1 El tiempo de vuelo, no la potencia

A diferencia de tecnologías basadas en potencia recibida (BLE, WiFi RSSI), el ranging UWB por Two-Way Ranging (TWR) mide directamente el **tiempo de vuelo** de la señal entre dos dispositivos y lo convierte en distancia (`d = t × c`). Esto requiere determinar con precisión de nanosegundos **cuándo llegó** la señal — no cuánta energía trae.

### 1.2 La respuesta al impulso del canal (CIR)

El receptor no ve "una señal": construye la **Channel Impulse Response (CIR)**, un perfil temporal de toda la energía que llegó, incluida la que rebotó en paredes, piso o techo antes de llegar al receptor:

```text
energía
  │      ①← primer camino (directo, más corto)
  │      █       ②← rebote en pared (llega después: recorrió más distancia)
  │      █       █     ③← rebote en el piso
  │▁▁▁▁▁▁█▁▁▁▁▁▁▁█▁▁▁▁▁█▁▁▁▁▁▁▁→ tiempo
```

Los pulsos UWB duran del orden de 2 ns, lo que separa caminos que difieran en más de ~60 cm de recorrido. Esa resolución temporal es la base de toda la robustez de UWB frente al multipath.

### 1.3 Detección del primer camino (*leading edge detection*)

Sobre el CIR corre un algoritmo de detección de borde de subida: se toma el **primer pico que supera el umbral de detección sobre el piso de ruido**, y **solo ese instante** se usa para calcular el tiempo de vuelo. El resto de los picos (los rebotes) se descartan **para el cronómetro**, pero su energía sigue usándose para demodular los datos de la trama — no se pierden, solo no participan del timing.

> **Consecuencia práctica: con línea de vista, el multipath no degrada significativamente la medición.** Los ecos llegan después del camino directo y quedan fuera de la ventana de detección. Esta es la razón estructural por la que UWB se eligió para ranging de precisión en interiores frente a alternativas de banda angosta, donde los ecos se superponen al camino directo y corrompen el timing.

**[Verificado 2026-08-06 contra el código fuente del SDK]** Esto no es solo teoría general: es exactamente lo que implementa el **driver de bajo nivel del DW3000** incluido en el SDK (`SDK/Firmware/.../Libs/dwt_uwb_driver/`). El chip mantiene registros de diagnóstico de la CIR (familias **Ipatov**, **STS1**, **STS2**) accesibles mediante dos funciones del driver:

```c
// deca_device_api.h — comentario de la propia API del fabricante
// "read Ipatov, STS1 and STS2 diagnostics registers [...] which can help in
//  determining if packet has been received in LOS or NLOS condition"
int  dwt_nlos_alldiag(dwt_nlos_alldiag_t *all_diag);
void dwt_nlos_ipdiag(dwt_nlos_ipdiag_t *index);   // -> index_fp_u32 (first path), index_pp_u32 (peak path)
```

`index_fp_u32` es, literalmente, el índice temporal del "primer camino" (①en el diagrama de arriba) e `index_pp_u32` el del "camino de máxima energía" (que en NLOS es distinto del primero). Estas dos funciones son la implementación real, en C, del mecanismo descripto en el §1.3 — y son de código abierto en este SDK (a diferencia de la capa FiRa/UCI, ver §3.2).

## 2. El caso NLOS: cuando el algoritmo es engañado

### 2.1 Qué pasa físicamente

En una condición **NLOS**, el camino directo está bloqueado — parcial o totalmente — por un obstáculo (pared, cuerpo, mobiliario). El algoritmo de detección sigue haciendo exactamente lo mismo: busca el primer pico por encima del umbral. El problema es que ahora **ese primer pico visible ya es un rebote**:

```text
  │   ①bloqueado    ②← rebote (ahora es "el primero" detectable)
  │      ░          █        ③
  │▁▁▁▁▁▁░▁▁▁▁▁▁▁▁▁▁█▁▁▁▁▁▁▁▁█▁▁→
                    ↑
          timestamp tomado acá → distancia SOBREESTIMADA
```

El chip no tiene manera de saber que existió un camino más corto que no llegó a superar el umbral. No hay ninguna señal física que le indique "faltó algo antes".

### 2.2 La asimetría del error: dato clave para cualquier algoritmo posterior

**Un rebote siempre recorre más distancia que el camino directo** (la geometría lo garantiza: la línea recta es el camino más corto entre dos puntos). Por lo tanto:

> **El error de NLOS es sistemáticamente positivo: la distancia medida en NLOS es siempre mayor o igual a la distancia real, nunca menor.**

Esta asimetría es la propiedad más explotable por cualquier sistema que necesite filtrar mediciones NLOS a posteriori (§5.2).

### 2.3 El caso intermedio: atenuación sin bloqueo total

Si el obstáculo atenúa la señal directa sin eliminarla del todo (una pared de durlock, un cuerpo humano), el camino directo puede seguir siendo el primer pico detectado, aunque débil. La distancia sale aproximadamente correcta (con un pequeño alargamiento por la velocidad de propagación reducida dentro del material), pero la energía total recibida queda dominada por los rebotes. Este escenario es precisamente el que revela el indicador `rsl`/`fsl` descripto en el §4.3.

## 3. Dos interfaces, un mismo stack: CLI vs. UCI

Este punto es importante porque el resto del documento distingue constantemente entre ambas: **el DWM3001C corre un único stack UWB** (el mismo motor FiRa/MAC), pero el SDK QM33 permite compilarlo detrás de **dos interfaces de host distintas**, que son proyectos de firmware separados:

| Interfaz | Cómo se controla | Rol de la PC/host | Usada en este proyecto |
|---|---|---|---|
| **CLI** (la que usa este proyecto) | Consola de texto plano por puerto serie; comandos como `INITF`, `RESPF`, `STAT` | La placa es autónoma: ejecuta la lógica FiRa por sí sola y expone una consola humana | ✅ Sí — [referencia-comandos-fw110.md](referencia-comandos-fw110.md), [DwmCliClient](../src/dwm3001c_cli/core/client.py) |
| **UCI** (*UCI: UWB Command Interface*) | Protocolo **binario**, definido por el FiRa Consortium, sobre el mismo puerto serie | La placa actúa como "módem" UWB puro: toda la lógica de sesión la decide un procesador externo (PC, Raspberry Pi) que arma y parsea paquetes UCI | ❌ No implementado en este proyecto (fuera de alcance actual) |

**[Verificado 2026-08-06 contra el código fuente del SDK]** Ambas interfaces son proyectos de firmware **separados y mutuamente excluyentes** — no coexisten en el mismo binario. El SDK trae el código fuente completo de ambos:

```text
SDK/Firmware/.../Projects/FreeRTOS/CLI/DWM3001CDK/     ← el firmware que corre en las placas de este proyecto
SDK/Firmware/.../Projects/FreeRTOS/UCI/DWM3001CDK/     ← firmware alternativo, mismo hardware, no usado acá
SDK/Firmware/.../Src/Apps/Src/uci/task_uci.c           ← tarea que parsea paquetes UCI entrantes por el puerto serie
SDK/Firmware/.../Src/Apps/Src/uci/uci_transport/       ← framing del protocolo binario sobre UART/USB
```

La guía de este proyecto (§5.2) ya menciona la vía UCI como alternativa para automatizar mediciones con el paquete `uwb-qorvo-tools` (`run_fira_twr`, `load_cal`, etc.) — herramientas Python que hablan el protocolo UCI con **una placa flasheada con el firmware UCI**, no con las placas CLI de este banco. Este documento usa esas mismas herramientas (`SDK/Tools/uwb-qorvo-tools/`) como fuente de referencia porque su código parsea el protocolo binario oficial y expone en Python, campo por campo, exactamente lo que las placas UCI emitirían — sin que haga falta flashear una placa adicional solo para esta investigación.

> **Nota de alcance:** implementar soporte UCI en este proyecto (flashear una tercera placa, hablar el protocolo binario desde Python) **no está en el plan de implementación actual** ([plan-implementacion.md](plan-implementacion.md)) y se marca como posible trabajo futuro (§6).

## 4. Qué prevé el protocolo y qué implementa Qorvo

### 4.1 FiRa/UCI sí define un indicador de NLOS — a nivel de protocolo binario

**[Verificado 2026-08-06 contra la documentación oficial del SDK]** La especificación **UWB UCI Message API** de Qorvo (`SDK/Documentation/uwb-stack/uwb-uci-messages-api-R12.7.0-405.pdf` — release `R12.7.0-405-gb33c5c4272`, **la misma versión de stack** que reportó la placa real en `STAT`, ver [captura real](referencia-comandos-fw110.md#12-stat)) define el layout de bytes exacto de la notificación `SESSION_INFO_NTF` para una medición TWR:

> **Tabla 5.51 — SESSION_INFO_NTF TWR measurement**
>
> | Tamaño (octetos) | Campo |
> |---|---|
> | 2\|8 | Responder address |
> | 1 | Status |
> | **1** | **NLoS** |
> | 2 | Distance |
> | 2 | AoA Azimuth |
> | 1 | AoA Azimuth FoM |
> | 2 | AoA Elevation |
> | 1 | AoA Elevation FoM |
> | ... | (Destination AoA, Slot index, RSSI, RFU) |

El campo `NLoS` es un byte, ubicado inmediatamente después de `Status` y antes de `Distance` — es decir, el protocolo lo trata como un dato de **primera clase** de cada medición, al mismo nivel que la distancia o el RSSI. La documentación de los encabezados C que consume el SDK (`Libs/uwbstack_libs/.../uwbmac/fira_helper.h`) confirma el significado de sus tres valores posibles:

```c
// fira_helper.h
/* @nlos: Indicates if the ranging measurement was in Line of Sight (LoS)
 * or Non-Line of Sight (NLoS): 0x00 = LoS, 0x01 = NLoS, 0xFF = Unable to determine. */
uint8_t nlos;
```

```c
// fira_region_params.h
#define FIRA_NLOS_NOT_SUPPORTED 0xff
```

Es decir: **el protocolo FiRa/UCI sobre el que corre el firmware de este módulo previó explícitamente la necesidad de reportar NLOS por medición**, con un valor reservado y legítimo (`0xFF`) para el caso en que el dispositivo no pueda o no intente determinarlo.

### 4.2 Pero el stack de Qorvo no lo calcula en esta plataforma

**[Verificado 2026-08-06 contra el código fuente del SDK]** El campo existe en el protocolo, tiene tamaño y posición fijos, y el parser del SDK lo decodifica — pero el stack UWB de Qorvo para el DW3000/QM33 **siempre devuelve el valor "no determinado"**. La propia herramienta de referencia del fabricante lo evidencia — `run_fira_twr` imprime el campo **hardcodeado como texto fijo**, sin siquiera mostrar el byte parseado:

```python
# SDK/Tools/uwb-qorvo-tools/lib/uwb-uci/uci/qorvo_msg.py
self.nlos = IsTrue(b.pop_uint(1))  # el byte SÍ se lee del paquete...

# ...pero el método que imprime el resultado no lo usa: lo reemplaza por un
# texto fijo, en todas las mediciones, sin excepción:
def __str__(self) -> str:
    return f"""...
            is nlos meas:       Unsupported
            ..."""
```

Y así aparece, textualmente, en **todos** los ejemplos de salida documentados en `SDK/Tools/uwb-qorvo-tools/scripts/fira/run_fira_twr/README.md` (se revisaron los cinco escenarios de ejemplo del documento), junto con status, distancia y ángulo de arribo (AoA) — el resto de los campos sí se calculan y muestran con su valor real.

**¿Por qué no podemos ver la causa exacta?** Porque la capa que arma esta notificación —la sesión FiRa/UCI (`uwbstack_bundle`)— se distribuye en este SDK **únicamente como biblioteca precompilada** (`.a`), sin código fuente:

```text
SDK/Firmware/.../Libs/uwbstack_libs/delivery/fira/Release/lib/arm-cortex-m33-hard_floating/
  └── libuwbstack_bundle_fira_...－rtos_R12.7.0-00405-gb33c5c4272.a   ← binario, sin .c
```

Solo se entregan los **encabezados** (`.h`) que definen la interfaz — de ahí que podamos citar la estructura de datos y el significado de `0xFF`, pero no el motivo de la implementación. Lo que sí es verificable con certeza es el **comportamiento observable**: en la versión de stack de este proyecto, el campo `nlos` de toda medición TWR llega en `0xFF` (*Unable to determine*) — un valor legítimo según la especificación, pero que en la práctica equivale a "no implementado para esta plataforma/versión".

> **Conclusión de esta sección:** la ausencia de indicador NLOS **no es una limitación del estándar FiRa, ni del silicio DW3000** — el chip sí computa internamente las magnitudes necesarias (§1, `dwt_nlos_alldiag`/`dwt_nlos_ipdiag`, código abierto). Es una decisión de la biblioteca de sesión FiRa/UCI de Qorvo (cerrada, distribuida solo como binario), vigente en la versión de SDK usada en este proyecto (QM33SDK-1.1.1, stack `R12.7.0-405-gb33c5c4272`).

## 5. Qué expone realmente cada interfaz, hoy

### 5.1 Tabla comparativa

| Interfaz / modo | Indicador disponible | ¿Refleja multipath? | Fuente |
|---|---|---|---|
| **CLI, sesión TWR** (`INITF`/`RESPF`, este proyecto) | Ninguno directo. `RSSI[dBm]` con `DIAG 1`; `RANGE_DIAGNOSTICS_NTF` (no documentada) con estado por trama del intercambio DS-TWR | Indirectamente (RSSI, dispersión) | [referencia-comandos-fw110.md](referencia-comandos-fw110.md) §5.2–5.3 |
| **CLI, modo LISTENER** (sniffer, este proyecto) | **`rsl`** (potencia total) y **`fsl`** (potencia del primer camino) por trama | **Sí, directamente** — es la magnitud más cercana a un indicador de multipath que expone el firmware CLI | [referencia-comandos-fw110.md](referencia-comandos-fw110.md) §2.3, §5.4 |
| **UCI, `SESSION_INFO_NTF`** (no implementado en este proyecto) | Campo `nlos` (1 byte, Tabla 5.51 del protocolo) — **presente en el paquete, pero fijo en `0xFF`** | No, en esta versión del stack | §4.1–4.2 de este documento |

**Precisión importante sobre CLI vs. UCI:** el `SESSION_INFO_NTF` que emite la consola **CLI** (el que capturamos en el banco real, ver [referencia-comandos-fw110.md](referencia-comandos-fw110.md) §5.2) es una **representación en texto plano**, elegida por el firmware CLI, de la misma notificación UCI subyacente — pero **no incluye el campo `nlos` en absoluto**, ni siquiera como `0xFF`. Es decir, hay dos filtros sucesivos entre el chip y lo que ve un usuario de este proyecto: primero el stack de Qorvo no calcula el valor (§4.2), y segundo, aunque lo calculara, el puente CLI→texto tendría que agregarlo a la línea de salida (algo que hoy no hace) para que fuera visible sin pasar por UCI binario.

### 5.2 El indicador real disponible hoy: `rsl − fsl` en modo LISTENER

**[Fuera del manual — criterio general de aplicaciones DW3000/Decawave]**: diferencias chicas (< ~6 dB) sugieren que el primer camino concentra la energía (LOS probable); diferencias grandes (> ~10 dB) sugieren energía dominada por rebotes. Ejemplo real capturado en el banco de este proyecto (2,20 m, interior):

```text
"rsl":-80.42,"fsl":-82.98   → diferencia  2,6 dB  → primer camino fuerte
"rsl":-80.96,"fsl":-91.94   → diferencia 11,0 dB  → primer camino débil
"rsl":-81.56,"fsl":-96.08   → diferencia 14,5 dB  → energía dominada por rebotes, en la misma sesión
```

**[Verificado 2026-08-06 contra el código fuente del SDK]** Este `rsl`/`fsl` **no es una estimación aproximada**: el firmware CLI lo calcula, en código abierto, a partir de las mismas magnitudes de diagnóstico de la CIR mencionadas en el §1.3 —confirma en la práctica que el mecanismo teórico de "primer camino vs. energía total" es exactamente el que corre en la placa—:

```c
// SDK/Firmware/.../Src/Apps/Src/listener/listener.c, listener_rssi_cal()
dwt_nlos_alldiag_t all_diag;
...
ip_rsl = 10 * log10((float)ip_cp / ip_n) + ip_alpha + log_constant + D;              // potencia TOTAL (Ipatov)
ip_fsl = 10 * log10(((ip_f1 + ip_f2 + ip_f3) / ip_n)) + ip_alpha + D;                 // potencia del PRIMER CAMINO
```

La variabilidad entre tramas consecutivas del mismo enlace es normal en interiores: personas, muebles y el propio cuerpo del observador modifican el canal en tiempo real.

**Nota sobre exclusividad de modos:** `LISTENER` es incompatible con `INITF`/`RESPF` (guía §1.3) — no se puede tener el indicador `rsl`/`fsl` de una placa **mientras** esa misma placa participa de una sesión de ranging. Ni el iniciador ni el respondedor tienen, durante el TWR, acceso a ese indicador para sí mismos; solo lo tendría una tercera placa actuando de sniffer en paralelo.

## 6. Dónde queda entonces la detección de NLOS: en el host

Puesto que ninguna interfaz disponible en este proyecto (CLI) entrega un veredicto NLOS útil por medición —y, según lo verificado en §4, tampoco lo haría UCI en esta versión del stack—, la determinación, si el proyecto llegara a necesitarla, recae enteramente en el software que consume las mediciones (el "host": la PC, o en un futuro sistema de posicionamiento, la Raspberry Pi mencionada en la guía). Las técnicas aplicables, de menor a mayor sofisticación:

### 6.1 Detección de saltos discretos de posición

Un objeto físico no se teletransporta. Si la distancia reportada salta bruscamente entre muestras consecutivas (más de lo que la velocidad física plausible del objetivo permitiría en ese intervalo de tiempo), esa muestra es sospechosa. Filtros de seguimiento (media móvil, mediana, filtro de Kalman) la rechazan o la ponderan a la baja.

### 6.2 Explotar la asimetría del error (§2.2)

Dado que el NLOS **solo** puede sobreestimar, dentro de un conjunto de mediciones repetidas del mismo enlace, **las más cortas son sistemáticamente las más confiables**. Algunos filtros usan directamente un percentil bajo (p. ej. el mínimo, o el percentil 10) en lugar de la media, precisamente para minimizar el sesgo positivo introducido por eventuales rebotes NLOS intercalados.

### 6.3 Indicadores físicos por medición

Donde estén disponibles (modo LISTENER de este firmware, o RSSI/dispersión durante TWR), se combinan como señal de confianza: RSSI bajo, alta dispersión entre muestras cercanas, o rondas `RX_TIMEOUT` intercaladas son todas señales indirectas de un enlace deteriorado, coherente con NLOS u otras fuentes de degradación (distancia excesiva, interferencia).

### 6.4 Redundancia geométrica (la técnica más robusta, fuera del alcance de este proyecto)

En un sistema de posicionamiento con **múltiples anclas** midiendo al mismo objetivo, la posición queda sobredeterminada. Si la distancia reportada por una de las anclas es inconsistente con la solución geométrica que forman las demás, esa ancla se identifica como probable NLOS y se excluye (o se pondera a la baja) del cálculo de posición final. Esta técnica opera en la capa de fusión de datos, no en cada dispositivo individualmente — es la responsabilidad natural del host que combina las mediciones de todas las anclas.

## 7. Implicancias y trabajo futuro para este proyecto

| Situación | ¿Es un problema hoy? |
|---|---|
| **Calibración de antenna delay** (fase F4, objetivo 2 del proyecto) | No: se realiza deliberadamente en línea de vista, a distancia conocida y controlada. El multipath introduce dispersión entre muestras (por eso se promedian ~100), pero no invalida la calibración — ver [resultados-calibracion.md](resultados-calibracion.md) §5. |
| **Validación de comandos** (objetivo 1) | No aplica: no involucra juicios de LOS/NLOS. |
| **Soporte del modo UCI** | No implementado; el alcance actual del proyecto es exclusivamente CLI (ver [CLAUDE.md](../CLAUDE.md) §1.1: "cuando la placa corre el firmware CLI"). Si se agregara en el futuro, este documento (§3–5) ya deja relevado que **no aportaría un indicador NLOS mejor que el disponible hoy por CLI/LISTENER**, en esta versión de stack. |
| **Un futuro sistema de posicionamiento multi-ancla** (mencionado en la guía como caso de uso con Raspberry Pi) | Sí sería relevante: ahí las técnicas del §6 —en especial la redundancia geométrica del §6.4— dejarían de ser una curiosidad teórica y pasarían a ser parte necesaria del diseño de la capa de fusión. Está fuera del alcance actual del proyecto. |

## Anexo — Trazabilidad de las verificaciones

| Afirmación | Cómo se verificó |
|---|---|
| El firmware CLI no reporta NLOS en `SESSION_INFO_NTF` (texto) | Inspección de todas las capturas reales archivadas en `docs/validaciones/2026-08-06-capturas-comandos-crudas.txt` |
| `LISTENER` reporta `rsl`/`fsl` por trama | Captura real, banco a 2,20 m, 2026-08-06 (ver [referencia-comandos-fw110.md](referencia-comandos-fw110.md) §2.3) |
| `rsl`/`fsl` se calculan desde los mismos registros de diagnóstico de CIR usados para NLOS | `SDK/Firmware/.../Src/Apps/Src/listener/listener.c`, función `listener_rssi_cal()` (código abierto) |
| El driver DW3000 expone primitivas de primer camino / camino de pico (`index_fp_u32`/`index_pp_u32`) | `SDK/Firmware/.../Libs/dwt_uwb_driver/deca_device_api.h` y `dw3000/dw3000_device.c` (código abierto) |
| El protocolo UCI define el campo `NLoS` (1 octeto) en `SESSION_INFO_NTF` de tipo TWR, con posición exacta en el paquete | `SDK/Documentation/uwb-stack/uwb-uci-messages-api-R12.7.0-405.pdf`, Tabla 5.51 (documentación oficial, misma versión de stack que reportan las placas reales) |
| Los tres valores posibles del campo (`0x00`/`0x01`/`0xFF`) y la constante de "no soportado" | `Libs/uwbstack_libs/.../include/uwbstack_bundle/uwbmac/fira_helper.h` y `.../net/fira_region_params.h` |
| Qorvo no lo calcula (queda fijo en "no determinado") | `SDK/Tools/uwb-qorvo-tools/lib/uwb-uci/uci/qorvo_msg.py` (parser + `__str__`), y README de `run_fira_twr` (los 5 escenarios de ejemplo del documento) |
| La biblioteca que arma la notificación (`uwbstack_bundle`) es binaria, sin código fuente en este SDK | `SDK/Firmware/.../Libs/uwbstack_libs/delivery/fira/Release/lib/**/*.a` (solo `.a` + headers, sin `.c`) |
| El firmware UCI es un proyecto separado del CLI, no usado en este proyecto | `SDK/Firmware/.../Projects/FreeRTOS/UCI/` vs. `.../Projects/FreeRTOS/CLI/` |
| No existe volcado de CIR en ninguna herramienta Python del SDK | Recorrido de `SDK/Tools/uwb-qorvo-tools/scripts/` (calibración, configuración, tests PER/CW, ranging — ningún script de diagnóstico de CIR) |

> **[Fuera del manual]** El contenido teórico general de este documento (funcionamiento del CIR, *leading edge detection* como concepto, asimetría del error, técnicas de filtrado de host) no proviene del Developer Manual de Qorvo, sino de principios generales de sistemas UWB de la familia DW3000/Decawave y del estándar FiRa. Se marca en bloque acá, en lugar de línea por línea, porque el documento completo es de naturaleza teórica/explicativa y así se indica en el encabezado. Las afirmaciones puntuales sobre código y documentación del SDK están marcadas individualmente como **[Verificado — contra el SDK]** y trazadas en la tabla de arriba.
