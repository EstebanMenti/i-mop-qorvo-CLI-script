# Resultados de la calibración de antenna delay — firmware 1.1.0

> **Propósito:** dejar constancia formal de la primera calibración automática de distancia realizada con la herramienta (objetivo 2 del proyecto), con su evidencia y los valores finales de cada placa.
> **Alcance:** calibración por TWR en canal 9 (`ant0.ch9.ant_delay`), firmware CLI 1.1.0. La calibración es **por canal**: el canal 5 no queda calibrado por esta corrida.

---

## 1. Resultado

**La placa COM28 quedó calibrada: error de +36,1 cm → +1,3 cm** (tolerancia ±2 cm), en 3 correcciones automáticas más el sondeo de sensibilidad. La persistencia en NVM fue **verificada con ciclo de alimentación**: tras desconectar y reconectar la placa, la clave conservó el valor final.

| Placa | Rol | `ant0.ch9.ant_delay` | Estado |
|---|---|---|---|
| COM28 (serie `DD18183315C4`) | Responder — **calibrada** | 16371 (fábrica) → **16459** | Persistido en NVM ✓ |
| COM26 (serie `F55EA0AF0AC4`) | Initiator — referencia | 16375 (fábrica, sin tocar) | — |

> **Nota metodológica:** siguiendo el procedimiento oficial (guía §4.1), se calibra **un dispositivo por vez contra el otro como referencia**. El error sistemático del par queda absorbido por la placa calibrada. Si se desea calibrar COM26, se repite el procedimiento invirtiendo los roles.

## 2. Banco de pruebas

| Ítem | Detalle |
|---|---|
| Distancia real | **2,20 m** entre módulos, medida con cinta métrica |
| Montaje | Placas verticales, enfrentadas, línea de vista |
| Sesión FiRa | Canal 9, BPRF4, DSTWR, BLOCK 200 ms (defaults del firmware, juego completo de parámetros) |
| Muestras por iteración | 100 (todas las tandas: 100/100 SUCCESS) |
| Fecha | 2026-08-06 |

## 3. Corrida completa

| Iter | Delay | Media [cm] | Desvío [cm] | Error [cm] | Corrección |
|---|---|---|---|---|---|
| 0 (inicial) | 16371 | 256,1 | 6,3 | +36,1 | — |
| 1 (sondeo) | 16391 | 244,6 | 3,9 | +24,6 | +20 |
| 2 | 16434 | 231,7 | 3,1 | +11,6 | +43 |
| 3 | 16454 | 222,6 | 1,5 | +2,6 | +20 |
| 4 (final) | **16459** | 221,3 | 1,9 | **+1,3** | +5 |

**Sensibilidad medida: 0,573 cm/unidad** — sensiblemente distinta del valor teórico de la familia DW3000 (~0,47 cm/unidad **[Fuera del manual]**), lo que confirma la decisión de diseño de medir la sensibilidad con un escalón de sondeo en lugar de asumirla.

Tras la convergencia se ejecutó `SAVE` y se verificó el valor por relectura. La verificación de persistencia (ciclo de alimentación + relectura) dio **16459** ✓.

## 4. Evidencia archivada

| Archivo | Contenido |
|---|---|
| [validaciones/2026-08-06-calibracion-com28.md](validaciones/2026-08-06-calibracion-com28.md) | Reporte de calibración generado por la herramienta |
| [validaciones/2026-08-06-calibracion-com28.json](validaciones/2026-08-06-calibracion-com28.json) | El mismo reporte en formato máquina (iteraciones completas) |
| [validaciones/2026-08-06-backup-calkey-com28.json](validaciones/2026-08-06-backup-calkey-com28.json) | Respaldo del valor de fábrica previo a la escritura (16371) |

## 5. Hallazgos de esta campaña

1. **La sensibilidad real difiere del valor teórico** (0,573 vs ~0,47 cm/unidad). Cualquier procedimiento que asuma la equivalencia teórica convergerá más lento o peor.
2. **El desvío individual de las muestras** en un ambiente interior (multipath) fue de 6–10 cm — un orden de magnitud mayor que el error estándar de la media con n=100 (~1 cm). El criterio de validación del sondeo debe basarse en el **error estándar de la media**, no en el desvío individual (corrección aplicada en `autocal.py` y asentada en el plan §6.2).
3. Con `SAVE` posterior a la calibración, **la persistencia en NVM quedó confirmada** tras ciclo de alimentación (despeja la ambigüedad discutida en la guía §4.3 para este firmware; no se ensayó el caso sin `SAVE`).
4. El desvío de las mediciones **disminuyó al converger** la calibración (de 6,3 a ~1,5 cm).

## 6. Conclusión

El **objetivo 2 del proyecto (calibración del antenna delay por CLI) está cumplido**: el bucle automático converge en hardware real en pocas iteraciones, con respaldo y restauración seguros, y la calibración persiste en NVM. Restan las tareas de empaquetado de la interfaz (`dwm calibrate`, fase F5) y el cierre formal de F6.
