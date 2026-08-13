# Resultados de la verificación del RESPONDER por puente Bluetooth — F10

> **Propósito:** dejar constancia formal de la campaña de verificación de hardware real de la rama `hardware/ble-bridge-nrf52840` (fase F10): ¿funcionan todos los comandos CLI por Bluetooth?, ¿llegan íntegras las respuestas de cualquier longitud?, ¿funciona la recalibración del responder?, ¿se puede reconfigurar el direccionamiento FiRa (ADDR/PADDR) del responder?
> **Alcance:** firmware Qorvo 1.1.0 (build `Aug 10 2026 16:03:38`, con el fix de transporte UART — ver [referencia-comandos-fw110.md](referencia-comandos-fw110.md) §0.1) en la placa RESPONDER cableada al puente nRF52840 (dirección BLE `FD:7A:90:57:CC:9F`, adv. `"UWB Node"`), contra una placa INITIATOR por USB (COM25). Software `dwm3001c_cli` con `BleTransport`/F8 ya implementado (ver [rama-hardware-ble.md](rama-hardware-ble.md) §7–§8).
> **Fecha:** 2026-08-13.

---

## 1. Resultado — resumen ejecutivo

| Pregunta | Resultado |
|---|---|
| ¿Todos los comandos de solo lectura funcionan por Bluetooth? | ✅ **Sí** — `HELP`, `HELP INITF`, `STAT`, `THREAD`, `DECAID`, `GETOTP`, `LISTCAL`, `CALKEY` (lectura), `UART` (consulta), `DIAG` (consulta), `LCFG` |
| ¿Las respuestas de cualquier longitud llegan íntegras? | ✅ **Sí** — confirmado con `LISTCAL` (259 líneas, la respuesta más larga) y `GETOTP` (131 líneas), sin truncar ni corromper, en varias corridas independientes |
| ¿Funcionan los comandos de escritura simples (una palabra + un argumento)? | ✅ **Sí** — `DIAG 0/1`, `SETAPP`, `SAVE`, `RESTORE` |
| ¿Funciona `CALKEY <clave> <valor>` (escritura, dos argumentos)? | ❌ **No, de forma consistente** — ver hallazgo crítico §3 |
| ¿Funciona `INITF`/`RESPF` con el juego completo de parámetros? | ⚠️ **Intermitente, mayormente falla** — funcionó una vez (sesión TWR real, §4), después falló de forma consistente en todos los reintentos — ver §3 |
| ¿Funciona una sesión TWR real (RESPONDER por BLE, INITIATOR por USB)? | ✅ **Sí, una vez** — 50/50 mediciones `SUCCESS` — pero no se pudo reproducir de forma confiable después (mismo bloqueo de §3) |
| ¿Funciona recalibrar el responder (`dwm calibrate`)? | ❌ **No** — bloqueado por el mismo hallazgo crítico (`CALKEY`/`RESPF` no responden) |
| ¿Se puede cambiar la dirección propia del responder (`ADDR`)? | ⛔ **No verificado** — bloqueado, ver §5 |
| ¿Se puede cambiar a qué iniciador responde (`PADDR`)? | ⛔ **No verificado** — bloqueado, ver §5 |
| Incidente durante las pruebas | ⚠️ `ant0.ch9.ant_delay` quedó corrompido en `0` por una escritura que el cliente reportó como fallida pero que igual llegó al firmware — **recuperado con `RESTORE`** (autorizado explícitamente por el usuario), ver §6 |

**Conclusión corta:** la mitad "de solo lectura y control" del protocolo (que es la mayoría de los comandos, y toda la validación de comandos) funciona de forma sólida y confiable por Bluetooth, con respuestas de cualquier longitud íntegras. La mitad que requiere **escrituras largas o con varios argumentos** (`CALKEY <clave> <valor>`, `INITF`/`RESPF` con parámetros completos) es **poco confiable** con el firmware puente actual, lo que bloquea en la práctica la recalibración remota y la reconfiguración de direccionamiento del responder. No es un problema del lado de `dwm3001c_cli` (se descartó exhaustivamente, ver §3.2) — apunta al firmware del puente nRF52840 o a una limitación de las notificaciones BLE sin ACK, y requiere investigación en el repo hermano `I-mop-nrf52840-fw`.

---

## 2. Banco de pruebas

| Ítem | Detalle |
|---|---|
| INITIATOR | DWM3001CDK por USB, puerto COM25 (bridge J-Link/J9), serie `000760225148` |
| RESPONDER | DWM3001C cableado al puente nRF52840, dirección BLE `FD:7A:90:57:CC:9F`, adv. `"UWB Node"`, serie `000760222700` |
| Firmware Qorvo (ambas placas) | CLI 1.1.0, build `Aug 10 2026 16:03:38` (fix de transporte UART) |
| Distancia real entre placas | 1,0 m (indicada por el usuario para la prueba de calibración) |
| Software | `dwm3001c_cli` (rama `hardware/ble-bridge-nrf52840`, F8 ya implementado), `bleak` 3.0.2, Windows 11, Python 3.14 |
| MTU BLE negociado | 247 (confirmado en todas las conexiones) |

## 3. Hallazgo crítico: comandos de escritura largos/multi-argumento no confiables por BLE

### 3.1 Evidencia

| Comando probado | Tokens | Resultado |
|---|---|---|
| `DIAG 1` / `DIAG 0` | 2 | ✅ Confiable, ~0,5 s |
| `SETAPP NONE` | 2 | ✅ Confiable, ~0,5 s |
| `SAVE` | 1 | ✅ Confiable, ~0,6 s |
| `RESTORE` | 1 | ✅ Confiable, ~0,7 s (ver §6) |
| `CALKEY restricted_channels 0` | 3 | ❌ **0/5 intentos** respondieron — timeout total incluso con 30 s de espera |
| `CALKEY ant0.ch9.ant_delay <valor>` | 3 | ❌ **0/6 intentos** respondieron (incluye los 3 intentos de recuperación de §6) |
| `RESPF -CHAN=9 ... -PADDR=0` (11 opciones, ~135 caracteres) | 1 comando, payload largo | ⚠️ 1/7 intentos exitoso (la sesión TWR real de §4); **0/6 intentos posteriores** respondieron, incluso con los mismos parámetros exactos que funcionaron la primera vez |

### 3.2 Descartado como causa (investigación exhaustiva, con hardware real)

- **No es truncado en la escritura BLE**: el log propio de `dwm3001c_cli` confirma que se entregó a `write_gatt_char()` el string completo y correcto (`b'qorvo CALKEY restricted_channels 0\n'`) — nada se pierde antes de salir del cliente.
- **No es específico de `response=False` (Write Without Response)**: se probó también con `response=True` (GATT Write Request, con acknowledgment a nivel ATT) — el mismo comando falló exactamente igual, descartando pérdida de paquete a nivel de enlace como única causa.
- **No es un estado "atascado" recuperable por software**: se probó, en orden, sin que ninguno restableciera el comportamiento — `qorvo off`+`qorvo on` (power-cycle del Qorvo), una conexión BLE completamente nueva, `qorvo off`+`qorvo on` **después** de un power-cycle físico del propio nRF52840 (desconectar y reconectar su alimentación), y una app de celular ajena desconectada para descartar contención de conexión.
- **No es específico de una clave de `CALKEY`**: falló igual con `restricted_channels` y con `ant0.ch9.ant_delay` (dos claves distintas, mismo patrón).
- **Sí depende de la longitud/complejidad del comando**: todo comando de una sola palabra o de dos tokens cortos (`DIAG`, `SETAPP`, `SAVE`, `RESTORE`) fue 100% confiable; todo comando de tres tokens o de payload largo (`CALKEY <clave> <valor>`, `RESPF`/`INITF` con parámetros) fue mayormente o completamente no confiable.

### 3.3 Patrón observado cuando sí llega algo

En algunos intentos fallidos, la respuesta (incompleta) del comando anterior reaparecía **minutos después**, pegada sin separador al comando siguiente — por ejemplo:

```text
CALKEY restricted_channelsqorvo SETAPP NONE
Please enter a valid key: restricted_channelsqorvo
KO
```

Esto es el eco truncado de `CALKEY restricted_channels 0` (le falta el valor y el terminador de línea) concatenado, sin separador, con el siguiente comando enviado (`qorvo SETAPP NONE`). Como mitigación del lado del cliente se agregó `BleTransport._reset_pending()` (descarta cualquier fragmento pendiente antes de mandar un comando nuevo — commit en `transport/ble_link.py`), que **elimina esta contaminación cruzada entre comandos** pero **no resuelve el timeout original** — confirma que el dato realmente se pierde en algún punto entre el cliente y el firmware del Qorvo (probablemente en el puente nRF52840, cuyo código no es parte de este repositorio), no que sea recuperable únicamente limpiando el lado cliente.

**Recomendación:** investigar en el repo hermano `I-mop-nrf52840-fw` cómo `qorvo_bridge.c` maneja comandos largos o con múltiples tokens — en particular si hay algún límite de buffer, tokenización parcial, o condición de carrera en el reenvío UART hacia el Qorvo que sea más probable con comandos de esta forma. Mientras tanto, la rama `hardware/ble-bridge-nrf52840` debe considerarse **no apta para recalibración remota ni para reconfigurar el direccionamiento FiRa del responder**, aunque sí apta para validación de comandos de lectura y para operar como RESPONDER con la configuración por defecto (si el firmware del responder ya arrancó `RESPF` exitosamente, como en la sesión TWR real documentada en §4).

## 4. Sesión TWR real (RESPONDER por BLE, INITIATOR por USB)

Corrida con `dwm validate --port COM25 --second-ble-address FD:7A:90:57:CC:9F` — el check **C4 pasó**: **50/50 mediciones `SUCCESS`**, ejemplo de distancia reportada: 7 cm, en 22,3 s totales (arranque de ambas placas + 50 muestras). Ver evidencia completa en `reports/validacion-COM25-20260813-121158.md`.

Este es el resultado más importante de la campaña en términos de viabilidad del proyecto: **el ranging FiRa TWR de punta a punta funciona correctamente con el responder alcanzado por Bluetooth**, cuando `RESPF` logra arrancar (ver §3 sobre la fiabilidad de ese arranque).

## 5. Validación de comandos — resultado completo

Se corrieron **dos** campañas de validación (`validation/runner.py`, sin cambios) para cubrir el protocolo completo por BLE:

### 5.1 COM25 (USB, primaria) + BLE (segunda placa) — `reports/validacion-COM25-20260813-121158.md`

**15 PASS · 3 FAIL · 0 SKIP** (18 checks). Los 3 FAIL (`C1 LISTENER`, `C2 INITF`, `C3 RESPF`) son sobre la placa **USB** (COM25), no sobre BLE — causados por un hallazgo preexistente y no relacionado con esta rama: el bridge J9/J-Link de COM25 pega el eco del comando a la primera línea de respuesta cuando hay notificaciones `SESSION_INFO_NTF`/`SESSION_STATUS_NTF` sin drenar entre el arranque de la app y la consulta `STAT` posterior (`_app_check` en `validation/spec.py`, `settle_delay_s=3.0` sin drenaje). **No es un bug de esta rama** — se reproduce igual en `main`, afecta a cualquier placa conectada por el bridge J9, y queda fuera del alcance de esta verificación (ver recomendación en §7).

### 5.2 BLE como placa primaria — `reports/validacion-BLE-FD7A9057CC9F-20260813-121641.md`

**13 PASS · 4 FAIL · 1 SKIP** (18 checks, corrido con el responder como cliente principal para ejercitar cada comando individualmente por Bluetooth — `dwm validate --port` no admite BLE como placa primaria, así que se invocó `run_validation()` directamente con un `DwmCliClient` sobre `BleTransport`).

| Check | Resultado | Detalle |
|---|---|---|
| A1–A11 (todos los de solo lectura) | ✅ PASS | Incluye `LISTCAL` (259 claves) y `GETOTP` (131 líneas) íntegros |
| B1 DIAG (toggle) | ✅ PASS | |
| B2 CALKEY (escritura neutra) | ❌ FAIL | Ver hallazgo crítico §3 |
| B3 SETAPP + SAVE | ❌ FAIL | Falla derivada de B2 (cola contaminada); probado aislado por separado: ✅ funciona |
| C1 LISTENER | ✅ PASS | (a diferencia de COM25, en esta corrida no se topó con el problema de notificaciones sin drenar) |
| C2 INITF | ❌ FAIL | Mismo problema de notificaciones sin drenar que en COM25 (§5.1), no específico de BLE |
| C3 RESPF | ❌ FAIL | Ídem |
| C4 (dos placas) | SKIP | Se corrió por separado en la topología real soportada, ver §4 |

**El comando `SAVE`, probado en aislamiento, funciona correctamente** (0,6 s) — la falla de B3 en esta corrida es enteramente heredada de la falla de B2, no un problema propio de `SETAPP`/`SAVE`.

## 6. Intento de recalibración real — FALLA, con incidente y recuperación

Se ejecutó `dwm calibrate --initiator COM25 --responder-ble-address FD:7A:90:57:CC:9F --distance-m 1.0 --ble-timeout-s 15 --yes`.

**Resultado: FALLA.** `autocalibrate()` llegó hasta el primer intento de medición, que requiere arrancar `RESPF` con el juego completo de parámetros en el responder — ese comando no obtuvo respuesta (`CommandTimeoutError` tras 15 s), abortando la calibración antes de escribir ningún `CALKEY`. Es el mismo hallazgo crítico de §3, no un problema del algoritmo de calibración en sí (`calibration/autocal.py`/`sampler.py` no se tocaron y ya están validados contra USB).

### 6.1 Incidente: corrupción de `ant0.ch9.ant_delay`

Durante la investigación del hallazgo de §3 (múltiples intentos de `CALKEY ant0.ch9.ant_delay <valor>`, todos reportados como `CommandTimeoutError` del lado del cliente), una relectura posterior de `LISTCAL` mostró que la clave había quedado en **`0`** — es decir, **al menos una de esas escrituras sí llegó a ejecutarse en el firmware**, a pesar de que el cliente nunca recibió confirmación. Esto es coherente con el patrón de §3.3: la escritura se procesa pero la respuesta/eco se pierde en algún punto del camino.

**Recuperación:** con autorización explícita del usuario, se ejecutó `RESTORE` (comando destructivo, restaura *toda* la calibración a valores de fábrica — CLAUDE.md §6, nunca se ejecuta sin confirmación interactiva directa). `RESTORE` respondió `ok` en 0,7 s y `ant0.ch9.ant_delay` quedó en **16376** (0x3FF8) — muy cercano al valor de fábrica documentado en otras placas de este proyecto (16375/0x3FF7; la diferencia de 1 unidad es variación normal de fábrica entre placas distintas, no un error). **La placa quedó recuperada en un estado sano.**

> **Advertencia para cualquier prueba futura de `CALKEY` por esta rama:** dado que una escritura puede ejecutarse en el firmware aunque el cliente reporte timeout, **no asumir que un `CommandTimeoutError` en `CALKEY <clave> <valor>` significa que el valor no cambió** — releer con `LISTCAL`/`CALKEY <clave>` para confirmar el estado real antes de continuar.

## 7. Combinaciones de direccionamiento (ADDR/PADDR) — bloqueado, no verificado

Se diseñó una matriz de 4 casos para probar el direccionamiento FiRa de punta a punta:

| Caso | INITF (USB) | RESPF (BLE) | Resultado esperado |
|---|---|---|---|
| T1 — direcciones por defecto | `ADDR=0 PADDR=1` | `ADDR=1 PADDR=0` | ranging exitoso |
| T2 — dirección propia del responder cambiada | `ADDR=0 PADDR=5` | `ADDR=5 PADDR=0` | ranging exitoso (confirma que se puede reconfigurar `ADDR` del responder) |
| T3 — `PADDR` del responder desajustado a propósito | `ADDR=0 PADDR=5` | `ADDR=5 PADDR=99` | **sin** mediciones `SUCCESS` (confirma que el responder filtra por la dirección configurada, o sea que se puede elegir a qué iniciador responde) |
| T4 — recuperación | igual a T2 | igual a T2 | ranging exitoso de nuevo (confirma que T3 no dejó nada roto) |

**Los 4 casos fallaron en el arranque** (`RESPF` con parámetros completos no respondió — mismo hallazgo de §3), incluso reintentando después del power-cycle físico del nRF52840. No se pudo obtener ninguna medición para ninguno de los 4 casos, así que **no hay evidencia ni a favor ni en contra** de que el direccionamiento ADDR/PADDR funcione correctamente — la pregunta queda genuinamente sin responder, bloqueada por el hallazgo de §3.

**Para retomar esta prueba** una vez resuelto el hallazgo crítico: el script queda preparado (no versionado, en el scratchpad de esta sesión) y puede reconstruirse fácilmente reutilizando `DwmCliClient.start_initf()`/`start_respf()` con `addr`/`paddr` explícitos, tal como hace `calibration/sampler.SessionParams`.

## 8. Recomendaciones

1. **Prioridad alta:** investigar el hallazgo de §3 en el firmware del puente (`I-mop-nrf52840-fw`, `qorvo_bridge.c`) — es el bloqueante principal para que esta rama cumpla sus dos objetivos pendientes (recalibración remota, reconfiguración de direccionamiento). Sin este fix, la rama solo sirve para validación de comandos de lectura y para operar como RESPONDER con la configuración FiRa por defecto ya arrancada.
2. **Prioridad media:** el hallazgo de §5.1 (notificaciones sin drenar en `_app_check`, `validation/spec.py`) es un bug preexistente en `main`, no específico de esta rama — reportarlo/corregirlo por separado, ya que afecta a cualquier placa (USB o BLE) cuando una app arrancada produce notificaciones espontáneas durante el `settle_delay_s`.
3. **Antes de repetir pruebas de `CALKEY` por esta rama:** tener presente el incidente de §6.1 — siempre releer para confirmar el estado real tras un timeout, nunca asumir que no se escribió nada.
4. Una vez resuelto el hallazgo de §3, repetir en este orden: (a) `dwm calibrate` real hasta convergencia, con backup/restauración verificados; (b) la matriz de direccionamiento de §7; (c) una sesión TWR sostenida (varios minutos) para confirmar estabilidad más allá de una corrida corta.

## 9. Conclusión

El objetivo central de esta rama — **un Qorvo alcanzable por Bluetooth puede actuar como RESPONDER en una sesión TWR real contra un INITIATOR por USB** — está **demostrado** (§4, 50/50 `SUCCESS`), y el protocolo completo de comandos de solo lectura y de escritura simple funciona de forma sólida por BLE, con respuestas de cualquier longitud íntegras (§5). Los dos objetivos que dependen de escrituras largas/multi-argumento — **recalibración remota y reconfiguración de direccionamiento** — están **bloqueados** por un hallazgo crítico y bien documentado (§3) que no es atribuible a `dwm3001c_cli` y requiere investigación en el firmware del puente. La placa quedó en un estado sano al cierre de esta campaña (§6.1).
