# Arquitectura del software

> **Propósito:** describir el diseño del paquete `dwm3001c_cli`: capas, módulos, responsabilidades y flujo de datos.
> **Alcance:** diseño de referencia para la implementación descripta en [plan-implementacion.md](plan-implementacion.md). Cualquier desvío del diseño debe documentarse aquí en el mismo pull request.

---

## 1. Visión general

La herramienta es una aplicación de consola que se comunica con una o dos placas DWM3001CDK a través de puertos COM virtuales (USB CDC ACM, 115200 8N1). El diseño separa estrictamente cuatro capas, con dependencias en un solo sentido:

```
┌─────────────────────────────────────────────────────────┐
│  app/            CLI Typer: comandos, salida Rich       │
└──────────────┬──────────────────────────┬───────────────┘
               │                          │
┌──────────────▼───────────┐  ┌───────────▼───────────────┐
│  validation/             │  │  calibration/             │
│  suite de validación     │  │  muestreo TWR + bucle     │
│  de comandos + reportes  │  │  de calibración           │
└──────────────┬───────────┘  └───────────┬───────────────┘
               │                          │
┌──────────────▼──────────────────────────▼───────────────┐
│  core/           DwmCliClient, parsers, modelos         │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│  transport/      SerialLink (pyserial), descubrimiento  │
└─────────────────────────────────────────────────────────┘
```

**Reglas de dependencia:**

- `transport/` no conoce el protocolo CLI del firmware; solo mueve bytes/líneas.
- `core/` no conoce Typer ni Rich; expone objetos Python puros.
- `validation/` y `calibration/` no abren puertos directamente; reciben un `DwmCliClient` ya construido.
- `app/` es la única capa con salida a pantalla y entrada interactiva del usuario.

Esta separación permite testear todo lo que está por encima de `transport/` **sin hardware**, sustituyendo el transporte por uno simulado (`FakeTransport`) alimentado con capturas reales del firmware.

## 2. Módulos

### 2.1 `transport/`

| Módulo | Responsabilidad |
|---|---|
| `serial_link.py` | Clase `SerialLink`: apertura/cierre del puerto (115200 8N1, sin control de flujo), escritura de líneas con terminador correcto, lectura no bloqueante con timeout, buffer de líneas recibidas, registro (log) de todo el tráfico crudo TX/RX con marca de tiempo. Define el protocolo (interfaz `Transport`) que implementa también el `FakeTransport` de tests. |
| `discovery.py` | Enumeración de puertos COM (`serial.tools.list_ports`) y filtrado de placas DWM3001CDK por VID/PID (SEGGER J-Link CDC: VID `0x1366`; interfaz nRF USB: VID `0x1915`). Devuelve candidatos con puerto, descripción y número de serie. |

### 2.2 `core/`

| Módulo | Responsabilidad |
|---|---|
| `client.py` | Clase `DwmCliClient`: envía un comando y recolecta la respuesta hasta detectar fin (respuesta `ok`, error, o silencio por timeout); primitivas `stop()`, `stat()`, `save()`, `calkey_read()/calkey_write()`, `listcal()`, `start_initf()/start_respf()`, `ensure_mode_none()`; lectura continua de notificaciones `SESSION_INFO_NTF` durante una sesión de ranging. |
| `parsers.py` | Funciones puras de parseo de las salidas del firmware: `STAT` (bloque JSON `JSxxxx{...}`), `LISTCAL`/`CALKEY` (`clave: 0xVALOR (len: N)`), `SESSION_INFO_NTF` (distancia, estado, RSSI…), `DECAID`, `GETOTP`, `THREAD`. |
| `models.py` | Dataclasses tipadas: `DeviceInfo`, `CalKey`, `Measurement`, `RangingStats`, `ValidationResult`, etc. |
| `errors.py` | Jerarquía de excepciones con base `Dwm3001cError` (`TransportError`, `CommandTimeoutError`, `CommandRejectedError`, `UnexpectedModeError`, `CalibrationError`…). |

### 2.3 `validation/`

| Módulo | Responsabilidad |
|---|---|
| `spec.py` | Especificación declarativa de cada comando a validar: nombre, modo requerido (NONE / cualquiera), comando a enviar, patrón esperado de respuesta, efectos colaterales y pasos de limpieza. |
| `runner.py` | Ejecuta la especificación contra una placa real en orden seguro (primero comandos de solo lectura, después los que modifican estado, con restauración al final), y produce una lista de `ValidationResult`. |
| `report.py` | Serializa los resultados a consola (tabla Rich), JSON y Markdown en `reports/`. |

### 2.4 `calibration/`

| Módulo | Responsabilidad |
|---|---|
| `sampler.py` | Orquesta una sesión TWR entre dos placas (`RESPF` primero, `INITF` después), recolecta N mediciones válidas, descarta las de estado distinto de `SUCCESS` y calcula promedio, desvío estándar y tasa de éxito. |
| `autocal.py` | Bucle de calibración automática (ver [plan-implementacion.md](plan-implementacion.md) §F4): mide, calcula el error contra la distancia real, estima la sensibilidad real (mm por unidad de retardo) con un escalón conocido en la primera iteración, corrige `ant<x>.ch<y>.ant_delay` vía `CALKEY` y repite hasta converger o agotar iteraciones. Consolida con `SAVE` y verifica con `LISTCAL`. |

### 2.5 `app/`

| Módulo | Responsabilidad |
|---|---|
| `cli.py` | Aplicación Typer con subcomandos `ports`, `info`, `validate`, `calibrate`, `terminal`. Traduce excepciones del dominio a mensajes claros y códigos de salida. |
| `config.py` | Carga de configuración (valores por defecto + archivo YAML opcional + opciones de línea de comandos, en ese orden de precedencia). |
| `logging_setup.py` | Configuración de `logging`: consola (nivel INFO) y archivo en `logs/` (nivel DEBUG, incluye tráfico serie crudo). |

## 3. Decisiones de diseño

| # | Decisión | Justificación |
|---|---|---|
| 1 | Layout `src/` + `pyproject.toml` | Evita imports accidentales del árbol de trabajo; empaquetado estándar PEP 621. |
| 2 | Interfaz `Transport` + `FakeTransport` | Permite tests unitarios de parsers y del cliente sin placas conectadas, usando capturas reales como fixtures. |
| 3 | Parsers como funciones puras | Testeables de forma aislada; el cliente solo coordina E/S. |
| 4 | Especificación de validación declarativa | Agregar un comando nuevo a validar no requiere tocar el runner. |
| 5 | El bucle de calibración mide la sensibilidad en vez de asumir ~4,7 mm/unidad | El factor efectivo depende del firmware **[Fuera del manual]**; medirlo hace el algoritmo robusto (ver guía §4.2, paso 5). |
| 6 | `RESTORE` y borrados nunca se ejecutan automáticamente | Regla de seguridad del proyecto (ver [CLAUDE.md](../CLAUDE.md) §1.4 y §6). |

## 4. Flujo de datos: calibración automática

```
Usuario: dwm calibrate --initiator COM7 --responder COM8 --distance-m 2.00
   │
   ▼
app/cli.py ── construye 2 DwmCliClient (uno por puerto)
   │
   ▼
calibration/autocal.py
   │   1. STOP + STAT en ambas placas (modo NONE)
   │   2. LISTCAL → registra ant_delay inicial (backup en reports/)
   │   3. sampler: RESPF (resp) → INITF (init) → N muestras → STOP
   │   4. error = distancia_promedio − distancia_real
   │   5. si |error| ≤ tolerancia → fin (SAVE + verificación)
   │   6. si primera iteración → aplicar escalón conocido y medir sensibilidad
   │      si no → corrección = error / sensibilidad
   │   7. CALKEY ant<x>.ch<y>.ant_delay <nuevo> → volver a 3
   ▼
reports/calibracion-<fecha>.json + .md   (trazabilidad completa de iteraciones)
```
