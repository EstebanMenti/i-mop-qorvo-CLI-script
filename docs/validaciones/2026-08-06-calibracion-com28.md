# Reporte de calibración de antenna delay — DWM3001CDK

> **Placa calibrada:** COM28 · **Referencia:** COM26 · **Fecha:** 2026-08-06 13:25:17
> **Clave:** `ant0.ch9.ant_delay` · **Distancia real:** 220.0 cm

**Resultado:** CONVERGIÓ · delay 16371 → **16459** · sensibilidad 0.573 cm/unidad · guardado en NVM (SAVE)

| Iter | Delay | Media [cm] | Desvío [cm] | Error [cm] | Corrección aplicada |
|---|---|---|---|---|---|
| 0 | 16371 | 256.1 | 6.3 | +36.1 | — |
| 1 | 16391 | 244.6 | 3.9 | +24.6 | +20 |
| 2 | 16434 | 231.7 | 3.1 | +11.6 | +43 |
| 3 | 16454 | 222.6 | 1.5 | +2.6 | +20 |
| 4 | 16459 | 221.3 | 1.9 | +1.3 | +5 |

> **Verificación de persistencia (manual):** quitar y reponer la alimentación de la placa calibrada y confirmar con `LISTCAL` que la clave conserva el valor final (guía §4.3).
