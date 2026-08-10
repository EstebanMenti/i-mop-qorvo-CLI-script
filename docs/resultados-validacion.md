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
| Sintaxis de los flags `MULTI`/`HOP` de `INITF`/`RESPF` | ✅ Confirmada por el `HELP INITF` del propio firmware el 2026-08-06: `-MULTI`/`-HOP` sin valor, tal como asume el cliente (ver [referencia-comandos-fw110.md](referencia-comandos-fw110.md) §2.1). No ejercitados en sesión |
| VID del conector J9 (SEGGER J-Link, `0x1366`) | ✅ Confirmado por el usuario mediante validación manual propia (flasheo habitual de las placas por J9), 2026-08-10. No verificado mediante el `find_boards()` automatizado de este proyecto (ambas placas del banco se conectaron siempre por J20/Nordic) |
| Persistencia de calibración tras ciclo de alimentación | ✅ Verificada el 2026-08-06 (ver [resultados-calibracion.md](resultados-calibracion.md)) |

## 6. Conclusión

El **objetivo 1 del proyecto (validación de comandos CLI) está cumplido** para el firmware 1.1.0. El software queda apto para construir sobre él la calibración automática del retardo de antena (fase F4): la sesión TWR, la escritura verificada de claves y el parseo de mediciones están probados contra hardware real.

## 7. Verificación final con la herramienta terminada (F6, 2026-08-10)

Las secciones 1–6 documentan la campaña original (fase F3, 2026-08-06), corrida contra la biblioteca Python directamente. Para el cierre del proyecto (fase F6) se repitió la validación completa usando el **comando `dwm validate` real** (la interfaz de línea de comandos terminada en la fase F5), sin scripts intermedios:

```powershell
dwm validate --port COM26 --second-port COM28 --report-dir reports
```

**Resultado: 18 PASS · 0 FAIL · 0 SKIP** (exit code 0), incluida una sesión TWR completa entre ambas placas con 50/50 mediciones `SUCCESS`. Evidencia archivada en [validaciones/2026-08-10-validate-dwm-cli-final.md](validaciones/2026-08-10-validate-dwm-cli-final.md) y su [equivalente JSON](validaciones/2026-08-10-validate-dwm-cli-final.json).

> **Nota sobre el valor de `ant0.ch9.ant_delay` de la placa A (COM26).** Esta corrida reporta `0x3FFC` (16380), distinto del `0x3FF7` (16375) registrado en la campaña original del §2. La diferencia se debe a que **el usuario modificó la placa manualmente entre ambas fechas, fuera del alcance de este proyecto** — no es un hallazgo de firmware ni una regresión del software; se deja constancia para que quien lea este documento no confunda ambos valores.

Con esta corrida, el objetivo 1 queda verificado no solo a nivel de biblioteca sino con la herramienta final tal como la usará cualquier operador del banco.
