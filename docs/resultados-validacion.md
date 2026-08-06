# Resultados de la validación de comandos CLI — firmware 1.1.0

> **Propósito:** dejar constancia formal del resultado de la campaña de validación de los comandos CLI del DWM3001CDK (objetivo 1 del proyecto), con los hallazgos de firmware documentados y su evidencia.
> **Alcance:** firmware CLI **1.1.0** (build 13/08/2025, SDK QM33 — `DW3_QM33_SDK - FreeRTOS`). Los hallazgos son específicos de esta versión; al cambiar de firmware, repetir la campaña.

---

## 1. Resultado

**Los 18 checks de la suite pasan contra hardware real: 18 PASS · 0 FAIL · 0 SKIP** (corrida definitiva del 2026-08-06, dos placas conectadas).

Todos los comandos del *Developer Manual* fueron ejercitados y verificados: `HELP`, `STAT`, `STOP`, `THREAD`, `DECAID`, `GETOTP`, `LISTCAL`, `CALKEY` (lectura y escritura), `UART` (consulta), `DIAG` (consulta y toggle), `LCFG`, `SETAPP`, `SAVE`, `LISTENER`, `INITF`, `RESPF`, y una **sesión FiRa TWR completa entre dos placas** con 50/50 mediciones `SUCCESS`.

Quedaron fuera de la suite, por diseño (ver [plan-implementacion.md](plan-implementacion.md) §5.1): `RESTORE` (destructivo — pisa la calibración), `UART 0/1` (riesgo de perder la consola USB) y la escritura de OTP.

## 2. Banco de pruebas

| Ítem | Detalle |
|---|---|
| Placa A (initiator) | DWM3001CDK, serie `F55EA0AF0AC4`, puerto COM26 (conector J20, nRF USB) |
| Placa B (responder) | DWM3001CDK, serie `DD18183315C4`, puerto COM28 (conector J20, nRF USB) |
| Firmware | CLI 1.1.0, build Aug 13 2025 14:23:02 · Driver DW3XXX 08.19.02 · UWB stack R12.7.0-405 |
| Chip (placa A) | Device ID `0xdeca0302`, Part ID `0x4ef24713` |
| Software | `dwm3001c-cli` 0.1.0 (suite F3, `run_validation()`), Windows 11, Python 3.14 |
| Montaje | Placas sobre escritorio a ~15 cm (suficiente para validación funcional; **no** para calibración) |

## 3. Evidencia archivada

| Archivo | Contenido |
|---|---|
| [validaciones/2026-08-06-validacion-fw110.md](validaciones/2026-08-06-validacion-fw110.md) | Reporte completo de la corrida definitiva (tabla de 18 checks con detalle y duraciones) |
| [validaciones/2026-08-06-validacion-fw110.json](validaciones/2026-08-06-validacion-fw110.json) | El mismo reporte en formato máquina (incluye respuestas crudas) |

Los reportes de corridas de trabajo se generan en `reports/` (fuera de control de versiones); solo las corridas de referencia se archivan aquí.

## 4. Hallazgos: discrepancias entre el manual y el firmware 1.1.0

La campaña detectó **seis** diferencias respecto del *Developer Manual*. Todas están asentadas con nota fechada en la [guía de referencia](referencias/guia-cli-calibracion-dwm3001cdk.md) y contempladas por el software:

| # | Hallazgo | Impacto en el software |
|---|---|---|
| 1 | `STAT` no emite la línea `MODE:`; el JSON llega partido en varias líneas; hay eco del comando y un `ok` final | `parse_stat` tolera ambas variantes; el modo se deriva de `Current App` |
| 2 | El firmware usa **`KO`** como marcador de fin de respuesta con error (no documentado) | `send_command` corta la lectura en `ok`/`KO` |
| 3 | La clave `xtal_trim` del ejemplo del manual **no existe**; `LISTCAL` reporta 259 claves | Los checks usan claves reales (`ant0.ch9.ant_delay`) |
| 4 | **La forma de lectura `CALKEY <key>` está rota**: responde `KO` para cualquier clave, aun las listadas por `LISTCAL` | `calkey_read` cae automáticamente a filtrar `LISTCAL` |
| 5 | El valor de entrada de `CALKEY` se interpreta en **decimal** (verificado: `10` → `0x0a`) | Confirma el supuesto del plan; la escritura siempre se verifica releyendo |
| 6 | `SESSION_INFO_NTF` llega **partida en dos líneas** (continuación con `\r` residual); existen `SESSION_STATUS_NTF` no documentadas | `read_notifications` reensambla por balance de llaves e ignora las de estado |

Hallazgos operativos adicionales: tras `STOP` el firmware tarda un instante en volver a NONE (el cliente espera 0,3 s antes de verificar), y el retardo de antena de fábrica de la placa A es `ant0.ch9.ant_delay = 0x3FF7` (**16375**) — dato de referencia para la calibración.

## 5. Pendientes de verificación

| Ítem | Estado |
|---|---|
| Sintaxis de los flags `MULTI`/`HOP` de `INITF`/`RESPF` | Sin verificar (el manual no muestra ejemplos; el cliente asume `-MULTI`/`-HOP`) |
| VID del conector J9 (SEGGER J-Link, `0x1366`) | Sin verificar (ambas placas se conectaron por J20/Nordic) |
| Persistencia de calibración tras ciclo de alimentación | Se verifica en la fase F4/F6 (requiere intervención manual) |

## 6. Conclusión

El **objetivo 1 del proyecto (validación de comandos CLI) está cumplido** para el firmware 1.1.0. El software queda apto para construir sobre él la calibración automática del retardo de antena (fase F4): la sesión TWR, la escritura verificada de claves y el parseo de mediciones están probados contra hardware real.
