# Guía de uso — Calibración con ambos nodos por Bluetooth (GUI)

> **Propósito:** explicar cómo usar la pestaña **"Calibración BLE"** de
> `dwm-gui`, que permite calibrar el retardo de antena de un nodo Qorvo
> DWM3001C con **las dos placas conectadas por Bluetooth** (cada una vía su
> puente nRF52840), sin ningún cable USB.
> **Alcance:** rama `feature/gui-calibracion-ble-ambos-nodos` (basada en
> `hardware/ble-bridge-nrf52840`). Complementa
> [rama-hardware-ble.md](rama-hardware-ble.md) (setup del puente) y
> [resultados-calibracion.md](resultados-calibracion.md) (procedimiento).

---

## 1. Requisitos

| Elemento | Detalle |
|---|---|
| 2 nodos Qorvo DWM3001C | Cada uno cableado por UART a un puente nRF52840 con el firmware de `I-mop-nrf52840-fw` (cableado en [rama-hardware-ble.md](rama-hardware-ble.md) §3) |
| Provisioning previo | Cada nodo debe haber pasado **una única vez** por `dwm ble-provision --port COMx` conectado por USB (habilita `UART 1` + `SAVE`). Sin este paso el puente no puede hablarle |
| PC con Bluetooth | Adaptador BLE (Windows 10/11 verificado) |
| Instalación | `pip install -e .[gui,ble]` y luego `dwm-gui` |

Los dos nodos se colocan a la **distancia real conocida** que se va a ingresar
(recomendación del fabricante: ~2 m, línea de vista, verticales, lejos de
metal).

## 2. Paso a paso en la pestaña "Calibración BLE"

1. **Escanear** — botón "Escanear BLE": lista **todos** los dispositivos
   Bluetooth al alcance (no solo puentes), con nombre, dirección y RSSI.
2. **Filtrar** — el campo de texto acota la lista y los selectores a los
   dispositivos cuyo nombre o dirección contenga ese texto (sin distinguir
   mayúsculas). Ej.: escribí `uwb` para quedarte solo con los puentes.
3. **Seleccionar los dos nodos**:
   - **Nodo INITIATOR (referencia)**: corre `INITF`; **no se modifica**.
   - **Nodo RESPONDER (a calibrar)**: corre `RESPF`; es la placa **que se
     calibra** — su clave `ant0.ch9.ant_delay` se reescribe durante el proceso.
   - Debajo de los selectores queda siempre a la vista el rótulo
     **"SE CALIBRA (RESPONDER): …"** para que no haya dudas de cuál placa se
     modifica. Los dos nodos deben ser dispositivos distintos (si eliges el
     mismo, el botón se deshabilita).
4. **Distancia real** — ingresar en el campo "Distancia real entre nodos"
   (en metros, misma cinta métrica con la que se midió el banco).
5. Parámetros avanzados (opcionales): muestras por medición (100), tolerancia
   (±2 cm), iteraciones máximas (6) y si se hace `SAVE` al converger.
6. **Iniciar calibración BLE** — pide confirmación (muestra qué placa se va a
   escribir y con qué valor actual) y arranca.

## 3. Qué se ve mientras calibra

| Elemento | Significado |
|---|---|
| Banner ámbar "Conectando a …" | Abriendo las dos conexiones BLE (puede tardar ~10 s la primera) |
| Banner ámbar "Calibrando…" + barra de progreso indeterminada | Sesión TWR corriendo; **en proceso** |
| Etiqueta grande "Distancia medida: N cm · media (últimas M): X cm" | Se actualiza con **cada medición** recibida del INITIATOR (media móvil de las últimas 20 mediciones exitosas). Si una ronda vence sin éxito muestra `RX_TIMEOUT` |
| Log "Iteración i: delay=… media=… error=…" | Una línea por medición del bucle: retardo escrito, distancia media, desvío y error contra la distancia real |
| Banner verde "✔ Calibración terminada: …" | **Convergió**: muestra el valor inicial → final de `ant0.ch9.ant_delay` y si quedó guardado en NVM (`SAVE`). Las placas quedan en modo NONE |
| Banner rojo "✖ Error: …" | Algo falló (enlace pobre, sin convergencia, salvaguarda). El valor original de la clave **se restaura automáticamente** en la placa y queda respaldado en `reports/backup-calkey-*.json` |

Al terminar (bien o mal) todos los controles se vuelven a habilitar y se puede
correr otra calibración sin reiniciar la aplicación.

## 4. Cómo funciona por dentro (notas técnicas)

- El puente nRF52840 **no reenvía notificaciones espontáneas** del Qorvo y el
  firmware CLI **no tiene comando que consulte la distancia** (ver
  [referencia-comandos-fw110.md](referencia-comandos-fw110.md) §5.1). Por eso
  esta herramienta usa el **sampler por polling**
  ([`calibration/poll_sampler.py`](../src/dwm3001c_cli/calibration/poll_sampler.py)):
  envía periódicamente un comando *anytime* (`THREAD`) al INITIATOR y parsea
  las notificaciones `SESSION_INFO_NTF` acumuladas que llegan junto con la
  respuesta (comportamiento verificado contra hardware real,
  [verificacion-comandos-responder-ble.md](verificacion-comandos-responder-ble.md) §3.4).
- El bucle de calibración es el mismo de siempre
  ([`calibration/autocal.py`](../src/dwm3001c_cli/calibration/autocal.py)),
  ahora con el sampler inyectable: mismo respaldo previo, misma salvaguarda de
  corrección máxima, misma restauración ante error y mismo reporte en
  `reports/calibracion-*.json`.
- Los transportes BLE se abren y cierran dentro del worker de la calibración:
  si algo falla, **siempre** se cierran ambos antes de reportar el error.
- Reportes y backups se escriben en `reports/` igual que en la CLI.

## 5. Limitaciones conocidas

- El rol por BLE es siempre el descrito arriba (INITIATOR = referencia,
  RESPONDER = a calibrar); no hay modo de invertirlos desde la GUI.
- La reconexión BLE del puente tras ~7-8 s de inactividad es normal (ver
  [rama-hardware-ble.md](rama-hardware-ble.md) §8); el transporte la maneja
  solo.
- Si el escaneo devuelve menos de 2 dispositivos, revisá que ambos puentes
  estén encendidos y que Windows no tenga la sesión Bluetooth ocupada por otra
  aplicación.
