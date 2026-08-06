# Plan de implementación — dwm3001c-cli

> **Propósito:** especificar con detalle suficiente la implementación del paquete `dwm3001c_cli`, de modo que pueda ejecutarla un desarrollador o una IA **sin tomar decisiones de diseño por su cuenta**.
> **Alcance:** cubre desde el andamiaje inicial hasta la verificación con hardware real. Lo que no está en este plan **no se implementa** sin consultar antes al responsable del proyecto.
> **Documentos rectores:** [CLAUDE.md](../CLAUDE.md) (reglas), [arquitectura.md](arquitectura.md) (diseño), [referencias/guia-cli-calibracion-dwm3001cdk.md](referencias/guia-cli-calibracion-dwm3001cdk.md) (comportamiento del firmware — **fuente de verdad**, citada abajo como "la guía").

---

## 0. Reglas obligatorias para la IA implementadora

1. **Leer primero** [CLAUDE.md](../CLAUDE.md) completo y la guía completa. Ante contradicción entre este plan y la guía sobre el comportamiento del firmware, **gana la guía** y se reporta la discrepancia.
2. **No inventar comportamiento del firmware.** Todo formato de respuesta que se parsee debe salir de los ejemplos de la guía (o de capturas reales que provea el usuario). Si falta un ejemplo, dejar el parser tolerante, marcar `TODO(verificar-con-hardware)` y reportarlo.
3. **No agregar** dependencias, módulos, subcomandos ni parámetros que no figuren en este plan.
4. **No ejecutar nunca automáticamente:** `RESTORE`, borrado de NVM/flash, escritura de OTP. `RESTORE` solo puede ejecutarse con el flag explícito `--allow-restore` **y** confirmación interactiva escribiendo el nombre del puerto.
5. Cada fase se desarrolla en su propia rama (`feature/f<N>-<nombre>`), termina con lint + tipos + tests en verde, y se integra por pull request según [CLAUDE.md](../CLAUDE.md) §5.
6. Idiomas: docstrings/comentarios/commits en **español**; identificadores en **inglés**.
7. Al terminar cada fase, actualizar la columna "Estado" de la tabla de fases (§1) en este mismo archivo.

## 1. Fases

| Fase | Contenido | Rama | Depende de | Estado |
|---|---|---|---|---|
| F0 | Andamiaje del paquete, errores, logging | `feature/f0-scaffolding` | — | ✅ Completada (PR #1) |
| F1 | Capa de transporte serie + descubrimiento | `feature/f1-transport` | F0 | ✅ Completada (PR #2, verificada con hardware: VID Nordic, terminador `\r\n`) |
| F2 | Cliente CLI, parsers y modelos | `feature/f2-core` | F1 | ✅ Completada (PR #3) |
| F3 | Suite de validación de comandos + reportes | `feature/f3-validation` | F2 | ✅ Completada (PR #4) |
| F4 | Muestreo TWR y calibración automática | `feature/f4-calibration` | F2 | ✅ Completada (PR #7) |
| F5 | Aplicación de consola (`dwm …`) | `feature/f5-app` | F3, F4 | Pendiente |
| F6 | Verificación con hardware real y cierre | `feature/f6-hardware-verification` | F5 | Pendiente |

---

## 2. F0 — Andamiaje

**Entregables:**

1. `src/dwm3001c_cli/__init__.py` con `__version__ = "0.1.0"` (única fuente de versión junto a `pyproject.toml`).
2. `src/dwm3001c_cli/core/errors.py` con la jerarquía:
   - `Dwm3001cError(Exception)` — base.
   - `TransportError(Dwm3001cError)` — fallas de puerto serie (no se pudo abrir, se desconectó).
   - `CommandTimeoutError(Dwm3001cError)` — sin respuesta dentro del timeout; el mensaje incluye puerto, comando y segundos esperados.
   - `CommandRejectedError(Dwm3001cError)` — el firmware respondió con error o sin `ok` cuando se esperaba `ok`.
   - `UnexpectedModeError(Dwm3001cError)` — el dispositivo no está en el modo requerido (p. ej. no llegó a NONE tras `STOP`).
   - `DeviceDiscoveryError(Dwm3001cError)` — no se encontraron placas o el puerto pedido no existe.
   - `CalibrationError(Dwm3001cError)` — el bucle de calibración no converge o detecta una condición insegura.
3. `src/dwm3001c_cli/app/logging_setup.py`: función `setup_logging(verbose: bool, log_dir: Path) -> None` que configura logger raíz con handler de consola (INFO; DEBUG si `verbose`) y handler de archivo `logs/dwm-<AAAAMMDD-HHMMSS>.log` (DEBUG). Formato de archivo: `%(asctime)s %(levelname)s %(name)s: %(message)s`.
4. Archivos `__init__.py` en todos los subpaquetes.
5. `tests/test_errors.py` mínimo (jerarquía correcta).

**Criterio de aceptación:** `pip install -e .[dev]` funciona; `ruff check`, `mypy src` y `pytest` pasan.

---

## 3. F1 — Transporte

### 3.1 `transport/serial_link.py`

Definir el protocolo (con `typing.Protocol`) `Transport`:

```python
class Transport(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def write_line(self, line: str) -> None: ...
    def read_line(self, timeout_s: float) -> str | None: ...  # None si venció el timeout
    @property
    def name(self) -> str: ...  # p. ej. "COM7"
```

Implementación `SerialLink(Transport)` sobre `pyserial`:

- Parámetros fijos del proyecto: **115200 baudios, 8 bits, sin paridad, 1 bit de stop, sin control de flujo** (guía §1.2). Constructor: `SerialLink(port: str, *, read_timeout_s: float = 0.1)`.
- `write_line` agrega el terminador `\r\n` **[Fuera del manual: verificar en F6 si basta `\r`]** y registra en el log `TX <puerto>: <línea>` (nivel DEBUG).
- `read_line` acumula bytes hasta `\n`, decodifica como `ascii` con `errors="replace"`, recorta `\r\n`, registra `RX <puerto>: <línea>` (DEBUG) y la devuelve. Implementar buffer interno para bytes parciales entre llamadas.
- Context manager (`__enter__`/`__exit__`) que abre y cierra el puerto.
- Ante `serial.SerialException`, relanzar como `TransportError` con contexto.

### 3.2 `transport/discovery.py`

- `find_boards() -> list[BoardPort]` con `BoardPort` dataclass (`port: str`, `description: str`, `serial_number: str | None`, `interface_hint: str`).
- Usa `serial.tools.list_ports.comports()`. Se consideran candidatos los puertos con VID `0x1366` (SEGGER J-Link OB, conector J9) o VID `0x1915` (Nordic nRF USB, conector J20). `interface_hint` = `"jlink-uart"` o `"nrf-usb"`.
- Si no hay candidatos, devolver lista vacía (la capa `app` decide el mensaje de error).

### 3.3 Transporte simulado para tests

`tests/fakes.py`: `FakeTransport(Transport)` que recibe un guion `dict[str, list[str]]` (comando → líneas de respuesta) y una cola de notificaciones espontáneas para simular `SESSION_INFO_NTF`. Registra los comandos recibidos para aserciones.

**Tests F1:** apertura/cierre simulado (con `FakeTransport`), armado de líneas con bytes parciales, timeout devuelve `None`. Test `@pytest.mark.hardware` opcional que lista puertos reales.

**Criterio de aceptación:** cobertura de `serial_link` (lógica de framing) y `discovery` (filtrado por VID con `comports` mockeado) con tests verdes.

---

## 4. F2 — Núcleo: cliente, parsers, modelos

### 4.1 `core/models.py` (dataclasses `frozen=True`, tipadas)

| Clase | Campos |
|---|---|
| `DeviceInfo` | `mode: str`, `device: str`, `current_app: str`, `version: str`, `build: str`, `apps: tuple[str, ...]`, `driver: str`, `uwb_stack: str`, `raw: str` |
| `CalKey` | `name: str`, `value: int`, `length_bytes: int`, `raw: str` |
| `Measurement` | `sequence_number: int`, `block_index: int`, `mac_address: str`, `status: str`, `distance_cm: int \| None`, `rssi_dbm: float \| None`, `raw: str` |
| `RangingStats` | `n_requested: int`, `n_received: int`, `n_success: int`, `mean_cm: float`, `std_cm: float`, `min_cm: int`, `max_cm: int` |
| `ChipId` | `device_id: str`, `lot_id: str`, `part_id: str`, `soc_id: str` |
| `ValidationResult` | `command: str`, `sent: str`, `passed: bool`, `detail: str`, `response_lines: tuple[str, ...]`, `duration_s: float` |

### 4.2 `core/parsers.py` — funciones puras

Cada parser recibe `list[str]` (líneas crudas) o `str` y devuelve el modelo. Formatos según la guía:

| Función | Entrada (ejemplo de la guía) | Salida |
|---|---|---|
| `parse_stat(lines)` | `MODE: NONE` + bloque `JS0108{"Info":{...}}` (§2.1). El prefijo `JSxxxx` (4 dígitos hex = longitud) se descarta y el resto se parsea como JSON. | `DeviceInfo` |
| `parse_calkey_line(line)` | `xtal_trim: 0x32 (len: 1)` — clave con puntos/underscores, valor hex, longitud en bytes (§2.3). | `CalKey` |
| `parse_listcal(lines)` | Una línea `parse_calkey_line` por clave; ignorar líneas vacías y eco. | `dict[str, CalKey]` |
| `parse_session_info(line)` | `SESSION_INFO_NTF: {…, n_measurements=1 [mac_address=0x0001, status="SUCCESS", distance[cm]=91, …, RSSI[dBm]=-66.5]}` (§2.4). Implementar con regex tolerante: campos ausentes → `None`. `status != "SUCCESS"` ⇒ `distance_cm` puede faltar. | `Measurement` |
| `parse_decaid(lines)` | 4 líneas `Qorvo … = 0x…` (§2.2). | `ChipId` |
| `is_ok(lines)` | ¿Alguna línea es exactamente `ok` (case-insensitive, sin espacios)? | `bool` |

**Regla:** todo parser conserva la entrada cruda en el campo `raw` para diagnóstico. Ante entrada no reconocible lanzar `ValueError` con la línea ofensiva (la capa superior decide).

### 4.3 `core/client.py` — `DwmCliClient`

```python
class DwmCliClient:
    def __init__(self, transport: Transport, *, command_timeout_s: float = 2.0) -> None: ...
```

Comportamiento requerido:

- `send_command(cmd: str, *, quiet_period_s: float = 0.3, timeout_s: float | None = None) -> list[str]`: envía la línea, recolecta líneas de respuesta hasta que pase `quiet_period_s` sin líneas nuevas o venza `timeout_s`; descarta el eco del comando si aparece. Si no llegó **ninguna** línea, lanzar `CommandTimeoutError`.
- `stat() -> DeviceInfo` — envía `STAT` y parsea.
- `stop() -> None` — envía `STOP`; no exige `ok` (puede no haber app corriendo).
- `ensure_mode_none() -> None` — `stop()` + `stat()`; si `mode != "NONE"`, reintenta 1 vez y luego lanza `UnexpectedModeError`. **Todo comando de servicio/IDLE debe llamarse después de esto** (guía §1.3).
- `listcal() -> dict[str, CalKey]`, `calkey_read(key: str) -> CalKey`.
- `calkey_write(key: str, value: int) -> CalKey`: envía `CALKEY <key> <value>` (valor **decimal**, como el ejemplo del manual, guía §2.3), parsea la respuesta y **verifica releyendo** la clave; si el valor releído no coincide, lanzar `CommandRejectedError`. **[Fuera del manual: el formato de entrada se confirma en F6.]**
- `save() -> None` — requiere modo NONE; exige `ok`.
- `diag(enable: bool) -> None`, `decaid() -> ChipId`, `getotp() -> list[str]` (crudo), `thread() -> list[str]` (crudo), `help_cmd(cmd: str | None) -> list[str]` (crudo), `setapp(app: str) -> None` (validar `app ∈ {LISTENER, INITF, RESPF, NONE}`), `uart_status() -> list[str]` (solo lectura; **no** implementar escritura `UART 0/1` — riesgo de perder la consola).
- `start_initf(**params) -> None` / `start_respf(**params) -> None`: construyen la línea con la sintaxis `-OPCION=VALOR` (guion delante, guía §2.4). Solo aceptar como kwargs las opciones de la tabla 7.6: `chan, prfset, pcode, slot, block, round, rru, id, vupper, multi, hop, addr, paddr`; validar rangos según la guía antes de enviar.
- `read_notifications(duration_s: float | None, max_count: int | None, on_measurement: Callable[[Measurement], None] | None) -> list[Measurement]`: lee líneas y parsea las que empiecen con `SESSION_INFO_NTF`, ignorando el resto sin fallar.
- `restore() -> None`: **existe pero** su docstring advierte que pisa la calibración; ninguna otra función del paquete la llama.

**Tests F2:** con `FakeTransport` y fixtures tomadas textualmente de la guía (§2.1, §2.2, §2.3, §2.4): parseo de `STAT`, `LISTCAL`, `SESSION_INFO_NTF` (con y sin RSSI, con estado distinto de SUCCESS), `DECAID`; `ensure_mode_none` con y sin app corriendo; `calkey_write` verifica relectura; construcción de línea `INITF -CHAN=9 …`; validación de rangos que rechaza `CHAN=7`.

**Criterio de aceptación:** todos los formatos de ejemplo de la guía parsean; mypy estricto sin errores.

---

## 5. F3 — Suite de validación de comandos

### 5.1 `validation/spec.py`

Especificación **declarativa** (lista de dataclasses `CommandCheck`), en tres grupos que se ejecutan en este orden:

**Grupo A — Solo lectura (sin efectos):**

| # | Comando enviado | Modo previo | Criterio de éxito (todas las condiciones) |
|---|---|---|---|
| A1 | `HELP` | NONE | Respuesta no vacía; contiene al menos `STAT`, `STOP`, `SAVE`. |
| A2 | `HELP INITF` | NONE | Respuesta no vacía; menciona `INITF`. |
| A3 | `STAT` | NONE | Parsea a `DeviceInfo`; `mode == "NONE"`; `apps` contiene `INITF`, `RESPF`, `LISTENER`. |
| A4 | `THREAD` | NONE | Respuesta no vacía. |
| A5 | `DECAID` | NONE | Parsea a `ChipId`; `device_id` empieza con `0xdeca`. |
| A6 | `GETOTP` | NONE | Respuesta no vacía; contiene direcciones `0x01A` y `0x01C` (retardos de antena, guía §1.4). |
| A7 | `LISTCAL` | NONE | Parsea; contiene la clave `ant0.ch9.ant_delay` y ≥ 10 claves en total. |
| A8 | `CALKEY xtal_trim` | NONE | Parsea a `CalKey` con `length_bytes == 1`. |
| A9 | `UART` | NONE | Respuesta no vacía (solo consulta). |
| A10 | `DIAG` | NONE | Respuesta no vacía (solo consulta). |
| A11 | `LCFG` | NONE | Respuesta no vacía; contiene `CHAN` (guía §2.2: solo aplica a LISTENER). |

**Grupo B — Modifican estado, con restauración inmediata:**

| # | Secuencia | Criterio de éxito | Limpieza |
|---|---|---|---|
| B1 | `DIAG 1` → `DIAG` → `DIAG 0` | `ok` tras `DIAG 1`; la consulta refleja `1`. | `DIAG 0` siempre (incluso ante fallo). |
| B2 | `CALKEY xtal_trim <mismo valor leído>` | `ok` / relectura idéntica. Escribe **el valor que ya tenía** ⇒ sin efecto neto. | — |
| B3 | `SETAPP NONE` → `SAVE` | `ok` en ambos. (`NONE` es el estado deseado del banco; no se persigue restaurar el valor previo.) | — |

**Grupo C — Comandos de aplicación (requieren detener al final):**

| # | Secuencia | Criterio de éxito | Limpieza |
|---|---|---|---|
| C1 | `LISTENER` → esperar 3 s → `STAT` | `STAT` reporta app LISTENER corriendo. | `STOP` + confirmar NONE. |
| C2 | `INITF` → esperar 3 s → `STAT` | App INITF corriendo. (Sin par no habrá mediciones; **no** es criterio de fallo.) | `STOP` + confirmar NONE. |
| C3 | `RESPF` → esperar 3 s → `STAT` | App RESPF corriendo. | `STOP` + confirmar NONE. |
| C4 | *(solo si hay 2 placas)* `RESPF` en placa B, `INITF` en placa A, leer 10 s | Se reciben ≥ 1 `SESSION_INFO_NTF` con `status="SUCCESS"` y `distance_cm` entero. | `STOP` en ambas. |

**Fuera de la suite:** `RESTORE` (solo manual con `--allow-restore` + confirmación), `UART 0/1` (riesgo de perder consola), escritura de OTP (imposible/prohibida).

### 5.2 `validation/runner.py`

- `run_validation(client: DwmCliClient, *, second_client: DwmCliClient | None = None) -> list[ValidationResult]`.
- Antes de empezar: `ensure_mode_none()`. Cada check se cronometra; las excepciones se capturan y convierten en `ValidationResult(passed=False, detail=<motivo>)` **sin abortar la suite**, ejecutando siempre su limpieza.
- C4 se omite (resultado `SKIP` en `detail`) si no hay segunda placa.

### 5.3 `validation/report.py`

- `render_console(results)` — tabla Rich: comando, PASS/FAIL/SKIP (verde/rojo/amarillo), duración, detalle.
- `write_json(results, path)` y `write_markdown(results, path)` en `reports/validacion-<puerto>-<AAAAMMDD-HHMMSS>.{json,md}`. El Markdown sigue el estilo documental del proyecto (encabezado con placa, versión de firmware según `STAT`, fecha; tabla de resultados; sección con las respuestas crudas de los fallos).

**Tests F3:** runner completo contra `FakeTransport` con guion de respuestas correctas (todo PASS) y con respuestas rotas (FAIL sin abortar y con limpieza ejecutada); serialización JSON/Markdown.

**Criterio de aceptación:** la suite completa corre de punta a punta contra el transporte simulado; los reportes se generan en los tres formatos.

---

## 6. F4 — Calibración automática del antenna delay

### 6.1 `calibration/sampler.py`

```python
def collect_samples(initiator: DwmCliClient, responder: DwmCliClient, *,
                    n_samples: int, session_params: SessionParams,
                    timeout_s: float) -> RangingStats
```

- Orden de arranque: **primero `RESPF` en el responder, después `INITF` en el initiator** (guía §4.2 arranca el responder primero).
- Recolecta mediciones del **initiator** hasta juntar `n_samples` con `status == "SUCCESS"` o vencer `timeout_s` (calcular default: `n_samples × BLOCK × 3`).
- Al terminar (o ante cualquier excepción): `STOP` en ambas placas y confirmar NONE (`finally`).
- Si la tasa de éxito es < 50 % o se juntaron < `n_samples/2` muestras, lanzar `CalibrationError` con estadísticas parciales (enlace probablemente malo; no tiene sentido calibrar).
- `SessionParams`: dataclass con los parámetros FiRa a usar; default = valores por defecto del firmware (canal 9, BLOCK=200 ms). **Enviar siempre el juego completo de parámetros** en `INITF`/`RESPF` para evitar la trampa de reseteo de parámetros (guía §2.4, regla de los listings 7.7–7.10).

### 6.2 `calibration/autocal.py` — algoritmo

```python
def autocalibrate(device: DwmCliClient, reference: DwmCliClient, *,
                  real_distance_m: float, config: AutocalConfig) -> CalibrationReport
```

`AutocalConfig` (defaults entre paréntesis): `n_samples` (100 — guía §4.2 paso 4), `tolerance_cm` (2.0), `max_iterations` (6), `probe_step_units` (20), `max_total_correction_units` (1500), `channel` (9), `antenna_path` (0), `do_save` (True).

La clave a calibrar se construye como `ant{antenna_path}.ch{channel}.ant_delay` (guía §3.2). El **dispositivo a calibrar** es `device` (rol RESPF); `reference` actúa de INITF y **no se modifica**.

Pasos (implementar exactamente; toda iteración se registra en el reporte):

1. `ensure_mode_none()` en ambas. `stat()` para el reporte. Leer `delay_0 = calkey_read(key)` y **guardar backup** (`reports/backup-calkey-<puerto>-<fecha>.json`) antes de escribir nada.
2. **Medición inicial:** `stats_0 = collect_samples(...)`. `error_0_cm = stats_0.mean_cm − real_distance_cm`. Si `|error_0_cm| ≤ tolerance_cm` → ir al paso 6.
3. **Sondeo de sensibilidad** (primera corrección, guía §4.2 paso 5 **[Fuera del manual]**): escribir `delay_1 = delay_0 + probe_step_units` (sube el retardo ⇒ debe **bajar** la distancia). Medir `stats_1`. Calcular `sensitivity_cm_per_unit = (stats_0.mean_cm − stats_1.mean_cm) / probe_step_units`. Validaciones: si la sensibilidad resulta ≤ 0 o el cambio de distancia quedó por debajo del ruido (`< 2 × std`), lanzar `CalibrationError` (el escalón no produjo efecto medible; revisar montaje o aumentar `probe_step_units`).
4. **Iterar** (mientras `|error| > tolerance_cm` y queden iteraciones): `correction_units = round(error_cm / sensitivity_cm_per_unit)`; `delay_next = delay_actual + correction_units`. **Salvaguardas antes de escribir:** `delay_next > 0` y `|delay_next − delay_0| ≤ max_total_correction_units`; si se violan, `CalibrationError` y **restaurar `delay_0`**. Escribir con `calkey_write`, medir, recalcular `error`.
5. Si se agotaron `max_iterations` sin converger: **restaurar `delay_0`**, `CalibrationError` con el historial.
6. **Consolidación:** `ensure_mode_none()` → si `do_save`: `save()` → `calkey_read(key)` para confirmar el valor final → indicar al usuario en el reporte que la verificación definitiva de persistencia requiere **ciclo de alimentación + `LISTCAL`** (guía §4.3; no automatizable).
7. Devolver `CalibrationReport`: placa, clave, `delay_0`, delay final, sensibilidad medida, tabla de iteraciones (delay, media, desvío, error, corrección), convergió o no. Serializar a `reports/calibracion-<AAAAMMDD-HHMMSS>.{json,md}`.

> **Advertencia a preservar en el código:** aumentar el retardo de antena **reduce** la distancia reportada (guía §4.2 paso 5). El signo del algoritmo depende de esto; dejar test unitario que lo fije.

**Tests F4:** simular el firmware con un `FakeTransport` cuyo modelo interno genere distancias en función del delay escrito (sensibilidad simulada ~0,47 cm/unidad): convergencia en ≤ 3 iteraciones desde un error de +30 cm; no-convergencia restaura `delay_0`; salvaguarda de `max_total_correction_units`; tasa de éxito baja aborta antes de escribir.

**Criterio de aceptación:** el bucle converge contra el simulador y todos los caminos de error restauran el valor original.

---

## 7. F5 — Aplicación de consola

`src/dwm3001c_cli/app/cli.py` con Typer; entry point `dwm` (ya declarado en `pyproject.toml`). Subcomandos y opciones — **exactamente estos**:

| Subcomando | Opciones | Comportamiento |
|---|---|---|
| `dwm ports` | — | Tabla de placas detectadas (puerto, descripción, serie, interfaz). Si no hay, mensaje claro y exit code 1. |
| `dwm info` | `--port` (obligatoria) | `ensure_mode_none` + `STAT` + `DECAID` + `GETOTP` + `LISTCAL`, presentados con Rich. |
| `dwm validate` | `--port` (oblig.), `--second-port` (opcional, habilita C4), `--report-dir` (default `reports/`) | Corre la suite F3, muestra la tabla y escribe JSON+Markdown. Exit code 0 si todo PASS/SKIP, 1 si hay FAIL. |
| `dwm calibrate` | `--initiator` (oblig.), `--responder` (oblig.), `--distance-m` (oblig., float), `--samples` (100), `--tolerance-cm` (2.0), `--max-iterations` (6), `--channel` (9), `--no-save` | Ejecuta F4 calibrando **el responder** contra el initiator como referencia. Muestra progreso por iteración (Rich). Confirmación interactiva antes de la primera escritura de `CALKEY` mostrando valor actual y clave (se saltea con `--yes`). |
| `dwm terminal` | `--port` (oblig.) | Terminal interactivo: lo tipeado se envía, lo recibido se imprime con marca de tiempo; `Ctrl+C` sale limpio cerrando el puerto. |

Reglas transversales:

- Opción global `--verbose` (DEBUG en consola) y `--log-dir` (default `logs/`); llamar a `setup_logging` al inicio.
- `app/config.py`: precedencia **CLI > YAML (`--config archivo.yaml`) > defaults**. El YAML solo puede contener las mismas claves que las opciones de CLI.
- Toda `Dwm3001cError` se muestra como mensaje claro sin traceback (el traceback va al log); exit code 1. Errores de uso, exit code 2 (Typer).

**Tests F5:** con `typer.testing.CliRunner` y clientes/fábricas inyectadas con monkeypatch: `ports` sin placas, `validate` genera reportes, `calibrate` pide confirmación sin `--yes`, precedencia de configuración.

**Criterio de aceptación:** `dwm --help` y todos los subcomandos funcionan; tests verdes.

---

## 8. F6 — Verificación con hardware real y cierre

Con las dos placas conectadas (esto lo ejecuta el usuario junto con la IA, no es automatizable a ciegas):

1. `dwm ports` detecta ambas placas; anotar VID/PID reales observados y corregir `discovery.py` si difieren de lo previsto.
2. Confirmar el terminador de línea real (`\r\n` vs `\r`) y el formato de entrada de `CALKEY` (decimal vs hex) con `dwm terminal`; corregir los `TODO(verificar-con-hardware)` de F1/F2.
3. `dwm validate --port … --second-port …` con reporte completo; archivar el reporte como fixture de referencia en `tests/fixtures/` (sanitizado) y ajustar parsers si alguna salida real difiere de la guía. **Toda discrepancia entre la guía y el firmware real se documenta en `docs/referencias/guia-cli-calibracion-dwm3001cdk.md`** como nota fechada.
4. Calibración real a distancia conocida (~2 m, guía §4.1): `dwm calibrate …`; verificar convergencia; ciclo de alimentación manual y `dwm info` para confirmar persistencia del delay (guía §4.3).
5. Actualizar `README.md` (sección "Uso previsto" pasa a "Uso"), completar estados de fases en este plan y abrir PR final.

**Criterio de aceptación (Definition of Done del proyecto):**

- Suite de validación en verde contra hardware real (o FAILs justificados y documentados).
- Una calibración completa convergida y persistida, con reporte archivado.
- `ruff`, `mypy --strict`, `pytest` en verde; documentación sincronizada.

---

## 9. Resumen de valores por defecto (referencia rápida)

| Parámetro | Valor | Fuente |
|---|---|---|
| Puerto serie | 115200, 8N1, sin control de flujo | Guía §1.2 |
| Clave de calibración | `ant0.ch9.ant_delay` (canal 9, trayecto 0) | Guía §3.2 |
| Distancia de calibración | ~2,00 m (configurable, obligatoria por CLI) | Guía §4.1 |
| Muestras por medición | 100 | Guía §4.2 **[Fuera del manual]** |
| Tolerancia de convergencia | 2 cm | Decisión de proyecto (resolución de 1 cm por muestra) |
| Iteraciones máximas | 6 | Decisión de proyecto |
| Escalón de sondeo | +20 unidades (~9 cm esperados) | Decisión de proyecto, basada en ~4,7 mm/unidad **[Fuera del manual]** |
| Corrección total máxima | ±1500 unidades | Decisión de proyecto (salvaguarda) |
| Timeout de comando | 2 s | Decisión de proyecto |
