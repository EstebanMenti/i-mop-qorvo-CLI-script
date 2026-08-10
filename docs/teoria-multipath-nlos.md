# Multipath y NLOS en UWB: fundamento teórico y su implementación en el DWM3001C

> **Propósito:** explicar, a nivel de principios físicos y de protocolo, cómo el ranging UWB tolera el multipath, por qué falla en condiciones NLOS (*Non-Line-Of-Sight*), qué prevé el estándar FiRa al respecto y qué de eso está realmente implementado en el módulo Qorvo DWM3001C con el firmware CLI de este proyecto.
> **Alcance:** documento teórico, complementario a [referencia-comandos-fw110.md](referencia-comandos-fw110.md) (que documenta las salidas reales del firmware) y a la [guía de referencia](referencias/guia-cli-calibracion-dwm3001cdk.md) (comportamiento verificado contra el Developer Manual). Todo lo que no proviene de documentación oficial de Qorvo/FiRa se marca **[Fuera del manual]**, según la convención del proyecto.
> **Origen:** consolida una serie de preguntas y verificaciones realizadas el 2026-08-06, incluyendo capturas reales del firmware y una revisión del código fuente del SDK del fabricante (QM33SDK-1.1.1).

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

## 2. El caso NLOS: cuando el algoritmo es engañado

### 2.1 Qué pasa físicamente

En una condición **NLOS** (*Non-Line-Of-Sight*), el camino directo está bloqueado — parcial o totalmente — por un obstáculo (pared, cuerpo, mobiliario). El algoritmo de detección sigue haciendo exactamente lo mismo: busca el primer pico por encima del umbral. El problema es que ahora **ese primer pico visible ya es un rebote**:

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

Esta asimetría es la propiedad más explotable por cualquier sistema que necesite filtrar mediciones NLOS a posteriori (§4.2).

### 2.3 El caso intermedio: atenuación sin bloqueo total

Si el obstáculo atenúa la señal directa sin eliminarla del todo (una pared de durlock, un cuerpo humano), el camino directo puede seguir siendo el primer pico detectado, aunque débil. La distancia sale aproximadamente correcta (con un pequeño alargamiento por la velocidad de propagación reducida dentro del material), pero la energía total recibida queda dominada por los rebotes. Este escenario es precisamente el que revela el indicador descrito en el §3.2.

## 3. Qué prevé el protocolo y qué implementa Qorvo

### 3.1 FiRa sí define un indicador de NLOS — a nivel de protocolo

El estándar **FiRa UCI Generic Technical Specification** define, dentro de la notificación de datos de ranging (`RANGE_DATA_NTF`), un campo booleano por cada medición que indica si esa medición fue clasificada como NLOS.

**[Verificado 2026-08-06 contra el código fuente del SDK]** El parser Python del propio SDK de Qorvo confirma la existencia de este campo en el protocolo:

```python
# SDK/Tools/uwb-qorvo-tools/lib/uwb-uci/uci/qorvo_msg.py
self.nlos = IsTrue(b.pop_uint(1))  # Is a non-Line of sight measurement?
```

Es decir: **el protocolo FiRa sobre el que corre el firmware de este módulo previó explícitamente la necesidad de reportar NLOS por medición.**

### 3.2 Pero el stack de Qorvo no lo implementa en esta plataforma

**[Verificado 2026-08-06 contra el código fuente del SDK]** Pese a que el campo existe en el protocolo y el SDK lo parsea, el stack UWB de Qorvo para el DW3000/QM33 **no lo calcula**. La propia herramienta de referencia del fabricante lo evidencia — `run_fira_twr` imprime el campo **hardcodeado**, sin siquiera mostrar el valor del bit parseado:

```python
# SDK/Tools/uwb-qorvo-tools/lib/uwb-uci/uci/qorvo_msg.py, método __str__
is nlos meas:       Unsupported
```

Y así aparece, textualmente, en **todos** los ejemplos de salida documentados en `SDK/Tools/uwb-qorvo-tools/scripts/fira/run_fira_twr/README.md`, junto con status, distancia y ángulo de arribo (AoA) — el resto de los campos sí se calculan y muestran con su valor real.

> **Conclusión de esta sección:** la ausencia de indicador NLOS **no es una limitación del estándar FiRa, ni del silicio DW3000** (el chip sí computa internamente magnitudes relacionadas: potencia total y potencia del primer camino, ver §3.3). Es una decisión de implementación del stack de firmware de Qorvo, vigente en la versión de SDK usada en este proyecto (QM33SDK-1.1.1).

### 3.3 Qué SÍ expone el firmware, y en qué interfaz

| Interfaz / modo | Indicador disponible | Fuente |
|---|---|---|
| **CLI, sesión TWR** (`INITF`/`RESPF`) | Ninguno directo. `RSSI[dBm]` con `DIAG 1`; `RANGE_DIAGNOSTICS_NTF` (no documentada) con estado por trama del intercambio DS-TWR | [referencia-comandos-fw110.md](referencia-comandos-fw110.md) §5.2–5.3 |
| **CLI, modo LISTENER** (sniffer) | **`rsl`** (potencia total recibida) y **`fsl`** (potencia del primer camino) por cada trama — la magnitud más cercana a un indicador de multipath que expone este firmware | [referencia-comandos-fw110.md](referencia-comandos-fw110.md) §2.3, §5.4 |
| **UCI** (`RANGE_DATA_NTF`) | Campo `nlos` definido por el protocolo, **pero fijo/no soportado** en el stack de Qorvo (§3.2) | Este documento, §3.1–3.2 |

**Nota sobre exclusividad de modos:** `LISTENER` es incompatible con `INITF`/`RESPF` (guía §1.3) — no se puede tener el indicador `rsl`/`fsl` de una placa **mientras** esa misma placa participa de una sesión de ranging. Ni el iniciador ni el respondedor tienen, durante el TWR, acceso a ese indicador para sí mismos; solo lo tendría una tercera placa actuando de sniffer en paralelo.

El criterio de interpretación de `rsl − fsl` **[Fuera del manual — criterio general de aplicaciones DW3000/Decawave]**: diferencias chicas (< ~6 dB) sugieren que el primer camino concentra la energía (LOS probable); diferencias grandes (> ~10 dB) sugieren energía dominada por rebotes. Ejemplo real capturado en el banco de este proyecto (2,20 m, interior):

```text
"rsl":-80.42,"fsl":-82.98   → diferencia  2,6 dB  → primer camino fuerte
"rsl":-80.96,"fsl":-91.94   → diferencia 11,0 dB  → primer camino débil
"rsl":-81.56,"fsl":-96.08   → diferencia 14,5 dB  → energía dominada por rebotes, en la misma sesión
```

La variabilidad entre tramas consecutivas del mismo enlace es normal en interiores: personas, muebles y el propio cuerpo del observador modifican el canal en tiempo real.

## 4. Dónde queda entonces la detección de NLOS: en el host

Puesto que ninguna interfaz de este SDK entrega un veredicto NLOS por medición, la determinación —si el proyecto llegara a necesitarla— recae enteramente en el software que consume las mediciones (el "host": la PC, o en el futuro sistema de posicionamiento, la Raspberry Pi mencionada en la guía). Las técnicas aplicables, de menor a mayor sofisticación:

### 4.1 Detección de saltos discretos de posición

Un objeto físico no se teletransporta. Si la distancia reportada salta bruscamente entre muestras consecutivas (más de lo que la velocidad física plausible del objetivo permitiría en ese intervalo de tiempo), esa muestra es sospechosa. Filtros de seguimiento (media móvil, mediana, filtro de Kalman) la rechazan o la ponderan a la baja.

### 4.2 Explotar la asimetría del error (§2.2)

Dado que el NLOS **solo** puede sobreestimar, dentro de un conjunto de mediciones repetidas del mismo enlace, **las más cortas son sistemáticamente las más confiables**. Algunos filtros usan directamente un percentil bajo (p. ej. el mínimo, o el percentil 10) en lugar de la media, precisamente para minimizar el sesgo positivo introducido por eventuales rebotes NLOS intercalados.

### 4.3 Indicadores físicos por medición

Donde estén disponibles (modo LISTENER de este firmware, o RSSI/dispersión durante TWR), se combinan como señal de confianza: RSSI bajo, alta dispersión entre muestras cercanas, o rondas `RX_TIMEOUT` intercaladas son todas señales indirectas de un enlace deteriorado, coherente con NLOS u otras fuentes de degradación (distancia excesiva, interferencia).

### 4.4 Redundancia geométrica (la técnica más robusta, fuera del alcance de este proyecto)

En un sistema de posicionamiento con **múltiples anclas** midiendo al mismo objetivo, la posición queda sobredeterminada. Si la distancia reportada por una de las anclas es inconsistente con la solución geométrica que forman las demás, esa ancla se identifica como probable NLOS y se excluye (o se pondera a la baja) del cálculo de posición final. Esta técnica opera en la capa de fusión de datos, no en cada dispositivo individualmente — es la responsabilidad natural del host que combina las mediciones de todas las anclas.

## 5. Implicancias para este proyecto

| Situación | ¿Es un problema? |
|---|---|
| **Calibración de antenna delay** (fase F4, objetivo 2 del proyecto) | No: se realiza deliberadamente en línea de vista, a distancia conocida y controlada. El multipath introduce dispersión entre muestras (por eso se promedian ~100), pero no invalida la calibración — ver [resultados-calibracion.md](resultados-calibracion.md) §5. |
| **Validación de comandos** (objetivo 1) | No aplica: no involucra juicios de LOS/NLOS. |
| **Un futuro sistema de posicionamiento multi-ancla** (mencionado en la guía como caso de uso con Raspberry Pi) | Sí sería relevante: ahí las técnicas del §4 —en especial la redundancia geométrica del §4.4— dejarían de ser una curiosidad teórica y pasarían a ser parte necesaria del diseño de la capa de fusión. Está fuera del alcance actual del proyecto (validación CLI + calibración de una placa contra otra). |

## Anexo — Trazabilidad de las verificaciones

| Afirmación | Cómo se verificó |
|---|---|
| El firmware CLI no reporta NLOS en `SESSION_INFO_NTF` | Inspección de todas las capturas reales archivadas en `docs/validaciones/2026-08-06-capturas-comandos-crudas.txt` |
| `LISTENER` reporta `rsl`/`fsl` por trama | Captura real, banco a 2,20 m, 2026-08-06 (ver [referencia-comandos-fw110.md](referencia-comandos-fw110.md) §2.3) |
| FiRa UCI define un campo `nlos` en `RANGE_DATA_NTF` | `SDK/Tools/uwb-qorvo-tools/lib/uwb-uci/uci/qorvo_msg.py`, línea con el parseo del campo y su comentario |
| Qorvo no lo implementa (queda `Unsupported`) | Mismo archivo, método `__str__` de la clase de medición TWR, y README de `run_fira_twr` (todos sus ejemplos de salida) |
| No existe volcado de CIR en ninguna herramienta del SDK | Recorrido de `SDK/Tools/uwb-qorvo-tools/scripts/` (calibración, configuración, tests PER/CW, ranging — ningún script de diagnóstico de CIR) |

> **[Fuera del manual]** El resto del contenido teórico de este documento (funcionamiento del CIR, *leading edge detection*, asimetría del error, técnicas de filtrado de host) no proviene del Developer Manual de Qorvo, sino de principios generales de sistemas UWB de la familia DW3000/Decawave y del estándar FiRa. Se marca en bloque acá, en lugar de línea por línea, porque el documento completo es de naturaleza teórica/explicativa y así se indica en el encabezado.
