# Verificación BLE: reconexión transparente en `read_line()` — 2026-09-08

## Problema

Al calibrar con ambas placas por BLE (puentes nRF52840), la corrida abortaba con:

```
[error] BLE-CCEBFE5BC5E9: conexión BLE perdida esperando respuesta
```

**Causa raíz**: el puente nRF52840 cierra la conexión GATT de forma espontánea
(idle ~7-8 s o bajo tormenta de notificaciones — comportamiento normal del
puente, ver `docs/rama-hardware-ble.md` §8). `BleTransport.read_line()`
levantaba `TransportError` al detectar la caída, en lugar de reconectar; la
reconexión solo ocurría en el `write_line()` **siguiente**, demasiado tarde
para el comando en curso.

## Fix

`src/dwm3001c_cli/transport/ble_link.py` — `read_line()` ahora reconecta de
forma transparente (hasta `_MAX_READ_RECONNECTS = 3` veces por llamada) y
sigue esperando: el módulo Qorvo sigue encendido y acumulando notificaciones,
que llegan apenas vuelve la conexión. El tiempo de reconexión no consume el
presupuesto de `timeout_s` del que llama. Si la conexión sigue cayendo
persistentemente, recién ahí se propaga el error.

## Verificación con hardware real

- **Nodos**: UWB-Node-6 (`CC:EB:FE:5B:C5:E9`) y UWB-Node-8 (`F4:B7:F3:B1:93:1E`), ambos por BLE.
- **Procedimiento**: espera de 9 s de inactividad (provoca el corte del
  puente) → `STAT` (reconexión) → suite de validación completa
  (`run_validation` con segunda placa, incluye C4 de sesión TWR real).
- **Resultado**: **18/18 PASS**, incluyendo C4 con **30/40 mediciones SUCCESS
  (~262 cm)**.
- Durante la suite el puente se cortó espontáneamente una vez más
  (11:08:17, `conexión BLE cerrada` → `reconectando`): recuperado sin que la
  corrida lo notara. El error `conexión BLE perdida esperando respuesta` no
  volvió a aparecer.

## Cambios asociados en la pestaña "Validar" (GUI)

- La tabla tiene columna nueva **Dispositivo**: muestra la placa validada
  (`COM7` / `BLE-...`); en C4 muestra `placa A + placa B`.
- El label de estado indica en todo momento qué placa se está validando.
- Celdas de estado con color: **PASS** verde, **FAIL** rojo, SKIP gris.

## Tests

- `tests/test_ble_link.py`: reconexión a mitad de comando + error si la
  reconexión falla.
- `tests/test_gui_models.py`: columna Dispositivo (C4 = ambas placas) y
  colores PASS/FAIL/SKIP.
- Suite completa: 177 passed (ruff + mypy + pytest).
