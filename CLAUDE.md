# CLAUDE.md — Contexto y reglas del proyecto

> **Propósito de este archivo:** dar a cualquier asistente de IA (y a cualquier desarrollador nuevo) el contexto, las reglas de programación y las convenciones de trabajo de este repositorio. **Leer este archivo completo antes de modificar código o documentación.**

---

## 1. Contexto del proyecto

### 1.1 Qué es

Herramienta de línea de comandos en **Python** para comunicarse por puerto serie con el módulo UWB **Qorvo DWM3001C** montado en la placa de desarrollo **DWM3001CDK**, cuando la placa corre el **firmware CLI** del SDK QM33 (release de referencia: QM33SDK-1.1.1).

La herramienta tiene **dos objetivos funcionales**:

1. **Validación de comandos CLI:** ejecutar de forma automatizada todos los comandos documentados en el Developer Manual (`HELP`, `STAT`, `STOP`, `THREAD`, `RESTORE`, `LCFG`, `DIAG`, `DECAID`, `SAVE`, `SETAPP`, `GETOTP`, `UART`, `CALKEY`, `LISTCAL`, `INITF`, `RESPF`, `LISTENER`) y verificar que las respuestas coincidan con lo esperado, generando un reporte de resultados.
2. **Calibración del retardo de antena (*antenna delay*):** automatizar el bucle iterativo de calibración de distancia por TWR descripto en el cap. 14 del Developer Manual, usando dos placas conectadas a la misma PC (una como INITIATOR, otra como RESPONDER).

### 1.2 Hardware del banco de pruebas

- **Dos placas DWM3001CDK conectadas por USB a la misma PC** (Windows). Cada placa expone un puerto COM virtual (USB CDC ACM).
- Parámetros del puerto serie: **115200 baudios, 8N1, sin control de flujo**.
- El firmware responde `ok` a los comandos ejecutados correctamente y reporta las mediciones de ranging como notificaciones `SESSION_INFO_NTF` con `distance[cm]` entero en centímetros.

### 1.3 Documentación de referencia (leerla antes de tocar la lógica del protocolo)

| Documento | Ubicación | Uso |
|---|---|---|
| Guía CLI y calibración (verificada contra el Developer Manual) | `docs/referencias/guia-cli-calibracion-dwm3001cdk.md` | **Fuente de verdad** del comportamiento de los comandos, estados NONE/IDLE, claves de calibración y procedimiento de calibración. |
| Quick Start Guide DWM3001CDK | `docs/referencias/DWM3001CDK-quick-start-guide.pdf` | Conectores, puesta en marcha de la placa. |
| APS014 — Antenna Delay Calibration | `docs/referencias/APS014-antenna-delay-calibration.pdf` | Fundamentos teóricos de la calibración de retardo de antena. |
| APH301 — Hardware Design Guide | `docs/referencias/APH301-hardware-design-guide.pdf` | Contexto de hardware DW3000/QM33100. |
| Esquemático DWM3001C | `docs/referencias/DWM3001C-esquematico-pcb.pdf` | Referencia de circuito de la placa. |

**Regla:** ante cualquier duda sobre el comportamiento del firmware (sintaxis de un comando, modo requerido, formato de respuesta), la referencia es la guía verificada y, en última instancia, el Developer Manual oficial. **No inventar comportamiento del firmware.** Si algo no está documentado, marcarlo como supuesto a verificar contra hardware real.

### 1.4 Reglas de dominio críticas (errores frecuentes a evitar)

- Los comandos de servicio e IDLE (`CALKEY`, `LISTCAL`, `SAVE`, `RESTORE`, `SETAPP`, `GETOTP`, `DIAG`, `LCFG`, `DECAID`, `UART`) **solo funcionan en modo NONE**. Siempre enviar `STOP` y confirmar con `STAT` (`MODE: NONE`) antes de usarlos.
- `INITF`, `RESPF` y `LISTENER` **no pueden correr simultáneamente** en una misma placa.
- Si un comando de aplicación recibe **cualquier** parámetro, **todos los demás parámetros vuelven a su valor por defecto**. Nunca asumir que un parámetro guardado con `SAVE` sobrevive a un comando con parámetros parciales.
- La clave de calibración de distancia es `ant<x>.ch<y>.ant_delay` (default DWM3001CDK: `ant0.ch9.ant_delay`). **La calibración es por canal.**
- Aumentar el retardo de antena **reduce** la distancia reportada.
- `RESTORE` pisa la calibración con los valores por defecto y escribe NVM automáticamente. **Nunca ejecutar `RESTORE` en un flujo automatizado sin confirmación explícita del usuario.**
- `SAVE` no puede usarse durante una sesión de ranging activa.
- Los valores de `CALKEY`/`LISTCAL` se reportan en **hexadecimal** con longitud en bytes (`len`).

---

## 2. Reglas de programación

### 2.1 Lenguaje y versión

- Python **≥ 3.11**. Desarrollo dentro de un **entorno virtual** (`.venv/` en la raíz, nunca versionado).
- Empaquetado con **`pyproject.toml`** (PEP 621). Instalación en modo editable: `pip install -e .[dev]`.

### 2.2 Estilo y calidad

- **Formato y lint:** `ruff` (formateador + linter). Configuración en `pyproject.toml`. Línea máxima: 100 caracteres.
- **Tipado:** anotaciones de tipos obligatorias en toda función pública. Verificación con `mypy` (modo estricto en `src/`).
- **Docstrings:** obligatorios en módulos, clases y funciones públicas, **en español**, formato Google. Los identificadores (nombres de variables, funciones, clases, módulos) van **en inglés** — evita problemas de codificación y sigue la convención universal.
- **Comentarios:** en español, solo para explicar restricciones no evidentes (p. ej. "el firmware exige modo NONE para este comando"), nunca para narrar lo que el código ya dice.
- **Logging:** usar el módulo `logging` (nunca `print` fuera de la capa de presentación). Todo el tráfico serie crudo (TX y RX) debe poder registrarse en archivo para diagnóstico.
- **Errores:** excepciones propias del proyecto (jerarquía con base `Dwm3001cError`). Nunca capturar `Exception` sin re-lanzar o registrar. Los timeouts de comunicación serie deben producir mensajes accionables (puerto, comando enviado, tiempo esperado).
- **Sin efectos colaterales peligrosos:** ninguna función debe escribir claves de calibración, `SAVE` ni `RESTORE` sin que eso sea su propósito explícito y esté a la vista en su nombre.

### 2.3 Testing

- Framework: **pytest**. Los tests unitarios viven en `tests/` y **no requieren hardware**: la capa serie se simula con un transporte falso (fake/mock) que reproduce respuestas reales del firmware (capturas incluidas como fixtures).
- Los tests que requieren hardware real se marcan con `@pytest.mark.hardware` y se excluyen por defecto (`-m "not hardware"`).
- Todo parser (STAT, LISTCAL, SESSION_INFO_NTF, etc.) debe tener tests unitarios con salidas reales del firmware como casos.
- Antes de cada commit: `ruff check`, `ruff format --check`, `mypy src`, `pytest -m "not hardware"` deben pasar.

### 2.4 Dependencias

- Mínimas y justificadas. Base autorizada: `pyserial` (transporte), `typer` (CLI), `rich` (salida en consola/reportes), `pyyaml` (configuración). Dev: `pytest`, `ruff`, `mypy`.
- No agregar dependencias nuevas sin justificarlo en el pull request.

---

## 3. Estructura del repositorio

```
i-mop-qorvo-CLI-script/
├── CLAUDE.md                  ← este archivo
├── README.md                  ← presentación y uso del proyecto
├── LICENSE                    ← (si aplica)
├── .gitignore
├── pyproject.toml             ← metadatos, dependencias, config de ruff/mypy/pytest
├── docs/                      ← documentación del proyecto (ver docs/README.md)
│   ├── README.md              ← índice de la documentación
│   ├── arquitectura.md        ← diseño del software
│   ├── plan-implementacion.md ← plan detallado para implementar el código
│   └── referencias/           ← documentos del fabricante y guía verificada
├── src/
│   └── dwm3001c_cli/          ← paquete Python principal
│       ├── transport/         ← capa serie: descubrimiento de puertos, lectura/escritura
│       ├── core/              ← cliente CLI, parsers, modelos de datos
│       ├── validation/        ← suite de validación de comandos
│       ├── calibration/       ← muestreo TWR y bucle de calibración automática
│       └── app/               ← puntos de entrada de línea de comandos (typer)
└── tests/                     ← tests pytest (sin hardware por defecto)
```

**Reglas de estructura:**

- Layout `src/`: el paquete importable vive solo bajo `src/`. Prohibido poner módulos sueltos en la raíz.
- Dependencias entre capas, en un solo sentido: `app → validation/calibration → core → transport`. La capa `transport` no conoce el protocolo CLI; la capa `core` no conoce Typer ni Rich.
- Los reportes generados en tiempo de ejecución van a `reports/` (ignorado por git) y los logs a `logs/` (ignorado por git).

---

## 4. Reglas de documentación

- **Idioma:** toda la documentación del proyecto se redacta **en español**.
- **Formato:** Markdown, mismo estilo técnico que `docs/referencias/guia-cli-calibracion-dwm3001cdk.md`: títulos numerados, tablas para datos enumerables, bloques `> **Nota/Advertencia**` para avisos, bloques de código para comandos y salidas.
- Todo documento nuevo en `docs/` debe: (a) empezar con un encabezado que indique propósito y alcance, (b) agregarse al índice `docs/README.md`.
- Las afirmaciones sobre el comportamiento del firmware deben citar la sección del Developer Manual o la guía verificada. Lo que no proviene del manual se marca **[Fuera del manual]**, igual que en la guía.
- El `README.md` de la raíz se mantiene sincronizado con la realidad del código: si cambia la interfaz de uso, se actualiza en el mismo pull request.

---

## 5. Flujo de trabajo con Git

### 5.1 Ramas

- `main`: siempre estable; solo recibe merges por pull request.
- Ramas de trabajo con el formato `<tipo>/<descripcion-corta-kebab-case>`:
  - `feature/` — nueva funcionalidad (ej.: `feature/parser-session-info`)
  - `fix/` — corrección de errores
  - `docs/` — solo documentación
  - `chore/` — mantenimiento, tooling, CI
  - `refactor/` — reestructuración sin cambio de comportamiento

### 5.2 Commits — Conventional Commits en español

Formato: `<tipo>(<ámbito opcional>): <descripción en imperativo, minúscula, sin punto final>`

- Tipos permitidos: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `perf`.
- Ámbitos sugeridos: `transport`, `core`, `validation`, `calibration`, `app`, `docs`.
- La descripción va **en español**. Ejemplos:
  - `feat(core): agrega parser de notificaciones SESSION_INFO_NTF`
  - `fix(transport): corrige timeout de lectura en descubrimiento de puertos`
  - `docs: actualiza plan de implementación con fase de reportes`
- Commits atómicos: un cambio lógico por commit. No mezclar refactor con feature.
- No commitear: código comentado muerto, archivos generados, credenciales, `.venv/`, reportes ni logs.

### 5.3 Pull requests

- Todo cambio a `main` pasa por PR, incluso siendo un solo desarrollador (deja trazabilidad).
- El título del PR sigue el mismo formato que los commits.
- La descripción debe incluir: **qué** cambia, **por qué**, **cómo se probó** (incluyendo si se probó contra hardware real o solo con transporte simulado), y capturas de salida cuando aplique.
- Requisitos para mergear: lint, tipos y tests en verde; documentación actualizada si el cambio la afecta.
- Merge por *squash* si la rama tiene commits de corrección intermedios; merge normal si los commits son atómicos y valiosos.

---

## 6. Reglas para asistentes de IA

1. **No inventar comportamiento del firmware.** Ante una duda de protocolo, consultar `docs/referencias/` y citar la sección. Si no está documentado, decirlo explícitamente y proponer verificación con hardware.
2. **Seguir el plan:** la implementación del código se rige por `docs/plan-implementacion.md`. No agregar módulos, dependencias ni funcionalidades que no estén en el plan sin consultar antes al responsable del proyecto.
3. **No ejecutar acciones destructivas** sobre las placas (`RESTORE`, borrado de NVM, escritura de OTP) desde código automatizado sin confirmación explícita e interactiva del usuario.
4. Mantener la coherencia de idioma: documentación, docstrings, comentarios y commits en español; identificadores en inglés.
5. Ante ambigüedad en un requisito, **preguntar antes de implementar**; no decidir unilateralmente.
