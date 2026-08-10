# dwm3001c-cli — Validación y calibración del Qorvo DWM3001C por CLI

> **Estado:** proyecto completo (fases F0–F6). **Ambos objetivos funcionales cumplidos contra hardware real** (fw 1.1.0): validación de comandos 18/18 PASS, incluso con la herramienta `dwm` terminada ([docs/resultados-validacion.md](docs/resultados-validacion.md)) y calibración automática de *antenna delay* convergida y persistida — error de +36 cm a +1,3 cm ([docs/resultados-calibracion.md](docs/resultados-calibracion.md)). Detalle de fases en [docs/plan-implementacion.md](docs/plan-implementacion.md).
> **Alcance:** herramienta Python de línea de comandos para validar el firmware CLI del módulo **Qorvo DWM3001C** (placa de desarrollo **DWM3001CDK**) y automatizar la **calibración del retardo de antena** (*antenna delay*) por Two-Way Ranging.

---

## 1. Qué hace este proyecto

La placa DWM3001CDK, con el firmware **CLI** del SDK QM33 (release de referencia QM33SDK-1.1.1), expone una consola de comandos por puerto serie (USB CDC ACM, 115200 8N1). Este proyecto automatiza dos tareas sobre esa consola:

| Función | Descripción |
|---|---|
| **Validación de comandos** | Ejecuta todos los comandos documentados en el Developer Manual (`STAT`, `STOP`, `HELP`, `THREAD`, `DECAID`, `LISTCAL`, `CALKEY`, `SAVE`, `SETAPP`, `GETOTP`, `DIAG`, `LCFG`, `UART`, `INITF`, `RESPF`, `LISTENER`, etc.), verifica que las respuestas tengan el formato esperado y genera un reporte de resultados (consola + archivo). |
| **Calibración automática** | Con **dos placas** conectadas a la misma PC, ejecuta el bucle iterativo oficial de calibración de distancia: mide N muestras por TWR, promedia, ajusta la clave `ant<x>.ch<y>.ant_delay` con `CALKEY`, y repite hasta converger a la tolerancia configurada. Al final consolida con `SAVE` y verifica la persistencia. |
| **Utilidades** | Descubrimiento de puertos COM de las placas, terminal interactivo, volcado de información del dispositivo (`STAT`, `DECAID`, `GETOTP`, `LISTCAL`). |

## 2. Hardware requerido

- 1 placa **DWM3001CDK** para validación de comandos; **2 placas** para calibración (una como INITIATOR, otra como RESPONDER).
- Conexión por USB (conector **J20** *nRF USB*, o **J9** *interface MCU*) a una PC con Windows.
- Para calibrar: ambas placas montadas a una **distancia real conocida** (recomendación del fabricante: ~2 m), en línea de vista, verticales y alejadas de superficies metálicas.

## 3. Instalación

```powershell
# Clonar el repositorio y entrar a la carpeta
git clone <url-del-repo>
cd i-mop-qorvo-CLI-script

# Crear y activar el entorno virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Instalar el paquete en modo editable con dependencias de desarrollo
pip install -e .[dev]
```

## 4. Uso

Todos los comandos están verificados contra hardware real (ver [docs/resultados-validacion.md](docs/resultados-validacion.md) §7 y [docs/resultados-calibracion.md](docs/resultados-calibracion.md)):

```powershell
dwm ports                      # Lista las placas DWM3001CDK detectadas
dwm info --port COM7           # STAT + DECAID + GETOTP + LISTCAL de una placa
dwm validate --port COM7 --second-port COM8
                               # Corre la suite de validación de comandos y genera reporte
                               # (--second-port es opcional; habilita el check de sesión TWR)
dwm calibrate --initiator COM7 --responder COM8 --distance-m 2.00
                               # Bucle de calibración automática del antenna delay;
                               # pide confirmación antes de escribir (saltear con --yes)
dwm terminal --port COM7       # Terminal interactivo crudo contra la consola CLI
```

Opciones globales (van antes del subcomando): `--verbose` (logging DEBUG en consola) y `--log-dir` (carpeta de logs, default `logs/`). La mayoría de los subcomandos acepta además `--config archivo.yaml` para fijar sus opciones en un archivo (precedencia: línea de comandos > YAML > valor por defecto); ver `dwm <subcomando> --help` para el detalle de cada uno.

## 5. Documentación

Toda la documentación está en [docs/](docs/README.md):

| Documento | Contenido |
|---|---|
| [docs/README.md](docs/README.md) | Índice completo de la documentación |
| [docs/arquitectura.md](docs/arquitectura.md) | Diseño del software: capas, módulos y responsabilidades |
| [docs/plan-implementacion.md](docs/plan-implementacion.md) | Plan de implementación detallado, por fases, con criterios de aceptación |
| [docs/resultados-validacion.md](docs/resultados-validacion.md) | Acta de la validación de comandos: 18/18 PASS contra hardware real |
| [docs/resultados-calibracion.md](docs/resultados-calibracion.md) | Acta de la calibración de antenna delay: convergencia y persistencia verificadas |
| [docs/referencia-comandos-fw110.md](docs/referencia-comandos-fw110.md) | Referencia de cada comando CLI con la respuesta **real** capturada del firmware |
| [docs/referencias/](docs/referencias/README.md) | Guía verificada de CLI/calibración y documentos del fabricante (Qorvo) |
| [CLAUDE.md](CLAUDE.md) | Contexto, reglas de programación y convenciones de Git del proyecto |

## 6. Desarrollo

Convenciones completas en [CLAUDE.md](CLAUDE.md). Resumen:

- Layout `src/`, tipado estricto (`mypy`), formato y lint con `ruff`, tests con `pytest` (sin hardware por defecto).
- Ramas `feature/…`, `fix/…`, `docs/…`; commits *Conventional Commits* en español; todo cambio a `main` por pull request.

```powershell
# Verificaciones previas a un commit
ruff check . ; ruff format --check . ; mypy src ; pytest
```
