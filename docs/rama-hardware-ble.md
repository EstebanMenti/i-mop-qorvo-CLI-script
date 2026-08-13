# Rama `hardware/ble-bridge-nrf52840` — RESPONDER por puente Bluetooth + GUI

> **Propósito:** documentar el alcance, el hardware necesario y el flujo de
> trabajo de esta rama de larga vida, que **nunca se mergea a `main`**.
> **Alcance:** agrega un modo de operación donde el board RESPONDER se
> alcanza por un puente Bluetooth Low Energy (nRF52840) en vez de por USB, y
> una interfaz gráfica de escritorio (PySide6) para operar la herramienta de
> forma manual y automática.

---

## 1. Por qué esta rama existe y por qué no se mergea a `main`

`main` cubre el banco de pruebas estándar: dos placas DWM3001CDK conectadas
por USB a la misma PC. Esta rama agrega un banco alternativo donde el
RESPONDER es un Qorvo DWM3001C cableado por UART a una placa nRF52840 que lo
expone por Bluetooth (firmware del repo hermano
`I-mop-nrf52840-fw`, ya implementado y validado en hardware real). Ese
hardware específico (nRF52840 + cableado UART a un segundo Qorvo) no siempre
está disponible, y el código que depende de él (transporte BLE, GUI de
escritorio) no debe convertirse en un requisito para quien solo usa el flujo
USB-USB de `main`. Por eso queda aislado en una rama `hardware/` de larga
vida en vez de mergearse — ver la excepción documentada en
[`CLAUDE.md` §5.1](../CLAUDE.md).

## 2. Qué agrega esta rama sobre `main`

| Capacidad | Comando / módulo |
|---|---|
| Habilitar la salida UART física del Qorvo del lado BLE (paso único, por USB) | `dwm ble-provision --port COMx` |
| Descubrir el puente nRF52840 por Bluetooth | `dwm ble-scan` |
| Usar el puente BLE como RESPONDER en validación | `dwm validate --second-ble-address <addr>` |
| Usar el puente BLE como RESPONDER en calibración | `dwm calibrate --responder-ble-address <addr>` |
| GUI de escritorio (conexión, terminal manual, validación y calibración con gráfico en vivo) | `dwm-gui` |

El rol Bluetooth es **siempre RESPONDER**, nunca INITIATOR: la calibración y
la validación solo leen notificaciones `SESSION_INFO_NTF` del lado INITIATOR
(USB), nunca del lado RESPONDER — esto es compatible con una limitación del
puente nRF52840, que solo reenvía respuestas a lo que se le pide, no mensajes
espontáneos del Qorvo.

## 3. Hardware necesario

- 1 Qorvo DWM3001CDK por USB, como INITIATOR (igual que en `main`).
- 1 Qorvo DWM3001C cableado por UART a una placa nRF52840 (TX nRF→P0.08,
  RX Qorvo→P0.06 según el firmware puente), con el firmware de
  `I-mop-nrf52840-fw` flasheado, como RESPONDER.
- Ese mismo Qorvo, conectado por USB **una única vez**, para el paso de
  `dwm ble-provision` (ver §5).

## 4. Instalación

Esta rama agrega dependencias opcionales que `main` no tiene, para no
forzarlas a quien solo usa el flujo USB-USB:

```powershell
# Transporte Bluetooth (bleak)
pip install -e .[ble]

# GUI de escritorio (PySide6 + pyqtgraph)
pip install -e .[gui]

# Todo junto, incluyendo dependencias de desarrollo
pip install -e .[dev,ble,gui]
```

## 5. Puesta en marcha del RESPONDER Bluetooth

1. **Provisioning único** (mientras el Qorvo del lado BLE está conectado por
   USB): `dwm ble-provision --port COMx`. Habilita `UART 1` y hace `SAVE` —
   sin este paso el puente nRF52840 no puede hablarle por UART (de fábrica el
   Qorvo solo responde por USB).
2. Desconectar el Qorvo del USB, conectarlo por UART al nRF52840 (según el
   cableado documentado en `I-mop-nrf52840-fw`), y encender el conjunto.
3. `dwm ble-scan` para confirmar que el puente anuncia `"UWB Node"` por BLE.
4. Usar `--second-ble-address`/`--responder-ble-address` en `validate`/
   `calibrate`, o la GUI (`dwm-gui`).

## 6. Sincronización con `main`

Rama de larga vida: se actualiza trayendo cambios de `main` por **merge**
(nunca rebase, para no romper ramas cortas de fase abiertas contra esta
rama), pero nunca se mergea de vuelta a `main`.

```bash
git checkout hardware/ble-bridge-nrf52840
git fetch origin
git merge origin/main
git push origin hardware/ble-bridge-nrf52840
```

Cadencia: antes de empezar cada fase nueva (ver §7), y cada vez que `main`
tenga cambios en `core/`, `calibration/` o `validation/` (las capas que esta
rama reusa sin modificar su contrato). Conflictos esperables: mínimos, ya que
esta rama solo agrega opciones nuevas a comandos existentes y métodos nuevos,
no reescribe lógica compartida.

Las ramas cortas de trabajo dentro de esta rama siguen la convención habitual
(`feature/f7-ble-provision`, `feature/f8-ble-transport`, `feature/f9-gui`,
`feature/f10-hardware-verification`), pero sus PR apuntan **contra
`hardware/ble-bridge-nrf52840`**, no contra `main`.

## 7. Fases de implementación

| Fase | Contenido | Estado |
|---|---|---|
| F7 | `dwm ble-provision`: habilita `UART 1` + `SAVE` en el Qorvo del lado BLE (por USB) | pendiente |
| F8 | `transport/ble_link.py` (`BleTransport` sobre Nordic UART Service vía `bleak`), `transport/ble_discovery.py`, wiring en `app/cli.py` | pendiente |
| F9 | GUI de escritorio PySide6 (`src/dwm3001c_cli/gui/`): conexión, terminal manual, validación y calibración con gráfico en vivo | pendiente |
| F10 | Verificación end-to-end contra hardware real; resolución de los riesgos de la §8 | pendiente |

F7 va primero porque no depende de BLE (usa `SerialLink` normal) y es
precondición física de todo lo demás.

## 8. Riesgos e incertidumbres a verificar contra hardware real

No inventar comportamiento no documentado — esta tabla se actualiza con el
resultado real de F10.

| Riesgo | Por qué importa | Resultado |
|---|---|---|
| MTU efectivo de `bleak`/WinRT en Windows 11 con este nRF52840 | Si queda en 23 bytes, `RESPF`/`INITF` con todos sus parámetros se truncan al escribir | pendiente de verificar |
| Latencia real del puente (silencio 400 ms / límite duro 8000 ms documentados en el firmware) | Define si los timeouts del cliente Python alcanzan | pendiente de verificar |
| `qorvo off` — ¿corta la conexión BLE o solo apaga el módulo Qorvo? | No documentado en la especificación del firmware puente | pendiente de verificar |
| Reaparición del "eco pegado sin separador" ya visto en el bridge UART de J9 (`core/client.py`) | La lógica ya existe, pero nunca se ejerció con este puente | pendiente de verificar |
| Pairing Just Works — ¿requiere emparejamiento manual previo desde Windows? | Puede bloquear la conexión con un diálogo del sistema | pendiente de verificar |
| Texto exacto del marcador de timeout del puente (`"Error: sin respuesta del modulo Qorvo (timeout)"`) | Documentado en el repo hermano, no capturado en este repo aún | pendiente de verificar |
| Sin reconexión automática ante un corte BLE a mitad de una calibración larga | Decisión de diseño consciente (mismo criterio que `SerialLink`); BLE es más propenso a cortes transitorios que un cable | comportamiento esperado, fuera de alcance de esta rama |

## 9. Referencias

- Firmware del puente: `I-mop-nrf52840-fw/doc/00_BLE_Protocol_Specification.md`
  (Nordic UART Service, comando `qorvo <texto>`, UUIDs de servicio/RX/TX).
- Arquitectura y reglas de capas de este proyecto: [arquitectura.md](arquitectura.md),
  [`CLAUDE.md`](../CLAUDE.md).
