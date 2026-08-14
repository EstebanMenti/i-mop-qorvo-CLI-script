# Resultados de la verificación del RESPONDER por puente Bluetooth — F10

> **Propósito:** dejar constancia formal de la campaña de verificación de hardware real de la rama `hardware/ble-bridge-nrf52840` (fase F10): ¿funcionan todos los comandos CLI por Bluetooth?, ¿llegan íntegras las respuestas de cualquier longitud?, ¿funciona la recalibración del responder?, ¿se puede reconfigurar el direccionamiento FiRa (ADDR/PADDR) del responder?
> **Alcance:** firmware Qorvo 1.1.0 (build `Aug 10 2026 16:03:38`) en la placa RESPONDER cableada al puente nRF52840 (dirección BLE `FD:7A:90:57:CC:9F`, adv. `"UWB Node"`), contra una placa INITIATOR por USB (COM25). Software `dwm3001c_cli` con `BleTransport`/F8 ya implementado (ver [rama-hardware-ble.md](rama-hardware-ble.md) §7–§8).
> **Fecha:** 2026-08-13. La campaña tuvo dos tandas: una primera que encontró un hallazgo crítico bloqueante (§3), y una segunda **después de que el usuario actualizó el firmware del puente nRF52840**, que lo confirma resuelto (§3.4) y completa todo lo que había quedado bloqueado.

---

## 1. Resultado — resumen ejecutivo

| Pregunta | Resultado |
|---|---|
| ¿Todos los comandos de solo lectura funcionan por Bluetooth? | ✅ **Sí** — `HELP`, `HELP INITF`, `STAT`, `THREAD`, `DECAID`, `GETOTP`, `LISTCAL`, `CALKEY` (lectura), `UART` (consulta), `DIAG` (consulta), `LCFG` |
| ¿Las respuestas de cualquier longitud llegan íntegras? | ✅ **Sí** — confirmado con `LISTCAL` (259 líneas, la respuesta más larga) y `GETOTP` (131 líneas), sin truncar ni corromper, en varias corridas independientes |
| ¿Funcionan los comandos de escritura simples (una palabra + un argumento)? | ✅ **Sí** — `DIAG 0/1`, `SETAPP`, `SAVE`, `RESTORE` |
| ¿Funciona `CALKEY <clave> <valor>` (escritura, dos argumentos)? | ✅ **Sí, confiable** — tras la actualización de firmware del puente (§3.4): 4/4 intentos exitosos, y `dwm validate` en verde |
| ¿Funciona `INITF`/`RESPF` con el juego completo de parámetros? | ✅ **Sí, confiable** — tras la actualización de firmware: arranca correctamente de forma consistente en todas las corridas posteriores |
| ¿Funciona una sesión TWR real (RESPONDER por BLE, INITIATOR por USB)? | ✅ **Sí, reproducible** — 50/50 mediciones `SUCCESS` en dos corridas independientes (antes y después de la actualización de firmware) |
| ¿Funciona recalibrar el responder (`dwm calibrate`)? | ✅ **Sí** — corrida real completa, convergió: `ant0.ch9.ant_delay` 16376 → 16200, error final +1,0 cm sobre 100 cm reales, guardado en NVM (§6) |
| ¿Se puede cambiar la dirección propia del responder (`ADDR`)? | ✅ **Sí** — 40/40 mediciones `SUCCESS` con `RESPF ADDR=5` (default es `1`) (§7) |
| ¿Se puede cambiar a qué iniciador responde (`PADDR`)? | ✅ **Sí** — con `PADDR` desajustado a propósito, 0/40 `SUCCESS` (el responder correctamente no rangea con un iniciador no configurado); con `PADDR` correcto, 40/40 `SUCCESS` (§7) |
| Incidente durante la primera tanda (antes del fix de firmware) | ⚠️ `ant0.ch9.ant_delay` quedó corrompido en `0` por una escritura que el cliente reportó como fallida pero que igual llegó al firmware — **recuperado con `RESTORE`** (autorizado explícitamente por el usuario); luego recalibrado con éxito en la segunda tanda, ver §6 |

**Conclusión corta: los cuatro objetivos de esta fase (comandos por BLE, integridad de respuestas, recalibración remota, reconfiguración de direccionamiento) están verificados y funcionando contra hardware real.** La primera tanda de pruebas encontró un hallazgo crítico que bloqueaba toda escritura larga o de varios argumentos (`CALKEY <clave> <valor>`, `RESPF`/`INITF` con parámetros completos) — se descartó exhaustivamente como problema de `dwm3001c_cli` (§3.2) y apuntaba al firmware del puente nRF52840. El usuario actualizó ese firmware durante la sesión, y la segunda tanda de pruebas (§3.4 en adelante) confirma que **el problema está resuelto**: todo lo que antes fallaba ahora funciona de forma consistente y reproducible.

---

## 2. Banco de pruebas

| Ítem | Detalle |
|---|---|
| INITIATOR | DWM3001CDK por USB, puerto COM25 (bridge J-Link/J9), serie `000760225148` |
| RESPONDER | DWM3001C cableado al puente nRF52840, dirección BLE `FD:7A:90:57:CC:9F`, adv. `"UWB Node"`, serie `000760222700` |
| Firmware Qorvo (ambas placas) | CLI 1.1.0, build `Aug 10 2026 16:03:38` |
| Firmware del puente nRF52840 | Actualizado por el usuario entre la primera y la segunda tanda de pruebas (2026-08-13) — versión previa y posterior no capturadas por este repo, ver `I-mop-nrf52840-fw` para el detalle del cambio |
| Distancia real entre placas | 1,0 m |
| Software | `dwm3001c_cli` (rama `hardware/ble-bridge-nrf52840`, F8 ya implementado), `bleak` 3.0.2, Windows 11, Python 3.14 |
| MTU BLE negociado | 247 (confirmado en todas las conexiones, antes y después del fix) |

## 3. Hallazgo crítico (primera tanda) — RESUELTO por actualización de firmware

### 3.1 Evidencia original (firmware del puente sin actualizar)

| Comando probado | Tokens | Resultado |
|---|---|---|
| `DIAG 1` / `DIAG 0` | 2 | ✅ Confiable, ~0,5 s |
| `SETAPP NONE` | 2 | ✅ Confiable, ~0,5 s |
| `SAVE` | 1 | ✅ Confiable, ~0,6 s |
| `RESTORE` | 1 | ✅ Confiable, ~0,7 s |
| `CALKEY restricted_channels 0` | 3 | ❌ **0/5 intentos** respondieron — timeout total incluso con 30 s de espera |
| `CALKEY ant0.ch9.ant_delay <valor>` | 3 | ❌ **0/6 intentos** respondieron |
| `RESPF -CHAN=9 ... -PADDR=0` (11 opciones, ~135 caracteres) | 1 comando, payload largo | ⚠️ 1/7 intentos exitoso; **0/6 intentos posteriores** respondieron, incluso con los mismos parámetros exactos |

### 3.2 Descartado como causa del lado del cliente (investigación exhaustiva, con hardware real)

- **No era truncado en la escritura BLE**: el log propio de `dwm3001c_cli` confirmaba que se entregaba a `write_gatt_char()` el string completo y correcto.
- **No era específico de `response=False` (Write Without Response)**: se probó también con `response=True` (GATT Write Request, con acknowledgment a nivel ATT) — el mismo comando fallaba exactamente igual.
- **No era un estado "atascado" recuperable por software**: se probó `qorvo off`+`qorvo on`, una conexión BLE completamente nueva, y un power-cycle físico del propio nRF52840, sin que ninguno restableciera el comportamiento.
- **No era específico de una clave de `CALKEY`**: fallaba igual con `restricted_channels` y con `ant0.ch9.ant_delay`.
- **Sí dependía de la longitud/complejidad del comando**: los comandos de una sola palabra o de dos tokens cortos eran 100% confiables; los de tres tokens o payload largo fallaban mayormente o por completo.

### 3.3 Patrón observado cuando sí llegaba algo (antes del fix)

En algunos intentos fallidos, la respuesta (incompleta) del comando anterior reaparecía **minutos después**, pegada sin separador al comando siguiente:

```text
CALKEY restricted_channelsqorvo SETAPP NONE
Please enter a valid key: restricted_channelsqorvo
KO
```

Como mitigación del lado del cliente (útil independientemente del fix de firmware, defensiva ante cualquier notificación BLE perdida) se agregó `BleTransport._reset_pending()`: descarta cualquier fragmento pendiente antes de mandar un comando nuevo, para que un dato perdido nunca contamine el comando siguiente. Queda en el código (`transport/ble_link.py`).

### 3.4 Segunda tanda — tras la actualización de firmware del puente: RESUELTO

El usuario actualizó el firmware del puente nRF52840 y pidió repetir las pruebas. Resultado, en el mismo banco de pruebas y con el mismo software cliente (sin cambios relevantes en `dwm3001c_cli` más allá de la mitigación defensiva de §3.3):

| Comando | Antes del fix | Después del fix |
|---|---|---|
| `CALKEY ant0.ch9.ant_delay <valor>` (reescritura neutra) | 0/6 intentos | ✅ **4/4 intentos**, ~2,9–3,5 s cada uno |
| `RESPF` con parámetros completos | 1/7 intentos | ✅ Consistente en todas las corridas posteriores (arrancó en 8,2 s la primera vez tras el fix; luego siempre dentro de una sesión TWR exitosa) |
| `dwm validate` con BLE como placa primaria | 13 PASS · 4 FAIL · 1 SKIP | ✅ **17 PASS · 0 FAIL · 1 SKIP** (el único SKIP es C4, que necesita segunda placa) |
| `dwm validate` con COM25 primaria + BLE segunda | 15 PASS · 3 FAIL (C1/C2/C3, ver §5.1, no relacionado con BLE) | Igual — los 3 FAIL son el hallazgo preexistente de `validation/spec.py`, no el hallazgo de esta sección; C4 (TWR real) pasó de nuevo, 50/50 |

**Conclusión de esta sección: el hallazgo crítico estaba en el firmware del puente nRF52840, tal como se había concluido en la investigación de §3.2, y la actualización del usuario lo resolvió.** No se requirió ningún cambio adicional en `dwm3001c_cli` más allá de la mitigación defensiva ya mencionada.

## 4. Sesión TWR real (RESPONDER por BLE, INITIATOR por USB)

Confirmado en **dos** corridas independientes, antes y después de la actualización de firmware:

| Corrida | Resultado |
|---|---|
| Antes del fix (`reports/validacion-COM25-20260813-121158.md`) | 50/50 `SUCCESS`, ejemplo 7 cm, 22,3 s totales |
| Después del fix (`reports/validacion-COM25-20260813-163345.md`) | 50/50 `SUCCESS`, ejemplo 16 cm, 21,9 s totales |

**El ranging FiRa TWR de punta a punta funciona correctamente y de forma reproducible con el responder alcanzado por Bluetooth.**

## 5. Validación de comandos — resultado completo

### 5.1 COM25 (USB, primaria) + BLE (segunda placa)

**15 PASS · 3 FAIL · 0 SKIP** (18 checks), igual antes y después del fix de firmware del puente. Los 3 FAIL (`C1 LISTENER`, `C2 INITF`, `C3 RESPF`) son sobre la placa **USB** (COM25), no sobre BLE, y **no están relacionados con el hallazgo de §3**: el bridge J9/J-Link de COM25 pega el eco del comando a la primera línea de respuesta cuando hay notificaciones `SESSION_INFO_NTF`/`SESSION_STATUS_NTF` sin drenar entre el arranque de la app y la consulta `STAT` posterior (`_app_check` en `validation/spec.py`, `settle_delay_s=3.0` sin drenaje). **No es un bug de esta rama** — se reproduce igual en `main`, afecta a cualquier placa conectada por el bridge J9, y queda fuera del alcance de esta verificación (ver recomendación en §8).

### 5.2 BLE como placa primaria, antes del fix — `reports/validacion-BLE-FD7A9057CC9F-20260813-121641.md`

**13 PASS · 4 FAIL · 1 SKIP.** `B2 CALKEY` (escritura) y `C2 INITF`/`C3 RESPF` fallaron por el hallazgo de §3; `B3` falló por arrastre de `B2`. `SAVE` aislado funcionaba bien.

### 5.3 BLE como placa primaria, después del fix — `reports/validacion-BLE-FD7A9057CC9F-20260813-163516.md`

**17 PASS · 0 FAIL · 1 SKIP** (el SKIP es `C4`, que necesita una segunda placa — se corrió por separado, ver §4). Incluye `B2 CALKEY` (escritura, 5,2 s), `C2 INITF` (21,0 s) y `C3 RESPF` (20,6 s), los tres ahora en verde.

## 6. Recalibración real — CONVERGIÓ

Se ejecutó `dwm calibrate --initiator COM25 --responder-ble-address FD:7A:90:57:CC:9F --distance-m 1.0 --ble-timeout-s 15 --yes` después de la actualización de firmware.

**Resultado: CONVERGIÓ.** Reporte completo en `reports/calibracion-BLE-FD7A9057CC9F-20260813-164111.md`:

| Iter | Delay | Media [cm] | Desvío [cm] | Error [cm] | Corrección |
|---|---|---|---|---|---|
| 0 (inicial) | 16376 | 16,7 | 2,5 | -83,3 | — |
| 1 (sondeo) | 16396 | 8,5 | 2,1 | -91,5 | +20 |
| 2 | 16171 | 114,0 | 2,5 | +14,0 | -225 |
| 3 | 16205 | 97,9 | 2,0 | -2,1 | +34 |
| 4 (final) | **16200** | 101,0 | 2,0 | **+1,0** | -5 |

Sensibilidad medida: 0,407 cm/unidad. **Guardado en NVM (`SAVE`) confirmado.**

> **Bug menor encontrado y corregido de paso:** el mensaje final de éxito de `dwm calibrate` (`app/cli.py`) usaba el carácter Unicode "→", que provoca un `UnicodeEncodeError` y crashea el proceso **después** de que la calibración ya convergió y se guardó (no afecta el resultado, solo el mensaje de confirmación en consolas Windows con codificación heredada tipo `cp1252`). Corregido reemplazándolo por `->` (ASCII).

### 6.1 Incidente de la primera tanda: corrupción de `ant0.ch9.ant_delay` — ya recuperado y luego recalibrado

Durante la investigación del hallazgo de §3 (antes del fix de firmware), una escritura de `CALKEY ant0.ch9.ant_delay` reportada como `CommandTimeoutError` del lado del cliente en realidad se ejecutó en el firmware, dejando la clave en `0`. Se recuperó en el momento con `RESTORE` (autorización explícita del usuario) a `16376`, y esa fue la base sobre la que corrió la recalibración exitosa de esta sección (16376 → 16200).

> **Advertencia que sigue vigente:** una escritura puede ejecutarse en el firmware aunque el cliente reporte timeout. Ante cualquier duda, releer con `LISTCAL`/`CALKEY <clave>` para confirmar el estado real antes de continuar.

## 7. Combinaciones de direccionamiento (ADDR/PADDR) — VERIFICADO

Matriz de 4 casos, corrida después de la actualización de firmware, con ventanas de 8 s por caso:

| Caso | INITF (USB) | RESPF (BLE) | Resultado esperado | Resultado real |
|---|---|---|---|---|
| T1 — direcciones por defecto | `ADDR=0 PADDR=1` | `ADDR=1 PADDR=0` | ranging exitoso | ✅ 40/40 `SUCCESS` |
| T2 — dirección propia del responder cambiada | `ADDR=0 PADDR=5` | `ADDR=5 PADDR=0` | ranging exitoso (confirma que se puede reconfigurar `ADDR` del responder) | ✅ 40/40 `SUCCESS` |
| T3 — `PADDR` del responder desajustado a propósito | `ADDR=0 PADDR=5` | `ADDR=5 PADDR=99` | **sin** mediciones `SUCCESS` (confirma que el responder filtra por la dirección configurada) | ✅ **0/40** `SUCCESS` (40/40 recibidas, todas sin éxito — comportamiento correcto) |
| T4 — recuperación | igual a T2 | igual a T2 | ranging exitoso de nuevo (confirma que T3 no dejó nada roto) | ✅ 40/40 `SUCCESS` |

**Los 4 casos dieron el resultado esperado.** Queda demostrado de punta a punta que:
- **Se puede cambiar la dirección propia (`ADDR`) del responder BLE** y seguir rangeando correctamente contra un iniciador configurado con el `PADDR` correspondiente (T2, T4).
- **El responder respeta el direccionamiento**: si su `PADDR` no coincide con el `ADDR` real del iniciador, no produce mediciones `SUCCESS` — es decir, **se puede elegir a qué iniciador responde** (T3), y esa restricción no dañó nada de forma permanente (T4 volvió a funcionar con la configuración correcta).

## 8. Recomendaciones

1. **Ya no es prioridad alta**: el hallazgo de §3 (comandos de escritura largos/multi-argumento) está resuelto por la actualización de firmware del puente. Si vuelve a aparecer un patrón similar en el futuro (timeout total en comandos largos, sin mensaje de timeout del propio puente), revisar primero la versión de firmware del nRF52840 antes de investigar del lado de `dwm3001c_cli`.
2. **Prioridad media, sigue pendiente**: el hallazgo de §5.1 (notificaciones sin drenar en `_app_check`, `validation/spec.py`) es un bug preexistente en `main`, no específico de esta rama — reportarlo/corregirlo por separado, ya que afecta a cualquier placa (USB o BLE) cuando una app arrancada produce notificaciones espontáneas durante el `settle_delay_s`.
3. Mantener la advertencia de §6.1 presente para cualquier prueba futura de `CALKEY`: un timeout no garantiza que no se haya escrito nada.
4. Próximo paso natural: una sesión TWR sostenida (varios minutos) para confirmar estabilidad más allá de una corrida corta, y avanzar con F9 (GUI de escritorio).

## 9. Conclusión

**Los cuatro objetivos de la fase F10 quedaron verificados contra hardware real**: todos los comandos CLI funcionan por Bluetooth con respuestas de cualquier longitud íntegras (§5); una sesión TWR real funciona de forma reproducible con el responder por BLE (§4); la recalibración remota converge correctamente y persiste en NVM (§6); y el direccionamiento FiRa del responder (`ADDR` propio y `PADDR` del iniciador al que responde) es reconfigurable y se comporta como se espera, incluido el caso de rechazo cuando las direcciones no coinciden (§7). La rama `hardware/ble-bridge-nrf52840` cumple sus objetivos funcionales centrales. El único ítem abierto es un bug preexistente y no específico de esta rama en la suite de validación (§5.1, §8.2).
