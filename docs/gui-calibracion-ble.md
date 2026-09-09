# Guía de uso — Calibración y medición con ambos nodos por Bluetooth (GUI)

> **Propósito:** explicar cómo usar las pestañas **"Calibración BLE"** y
> **"Medir"** de `dwm-gui`, que permiten calibrar el retardo de antena (o
> simplemente medir distancia, sin calibrar) de un nodo Qorvo DWM3001C con
> **las dos placas conectadas por Bluetooth** (cada una vía su puente
> nRF52840), sin ningún cable USB.
> **Alcance:** rama `hardware/ble-bridge-nrf52840`. Complementa
> [rama-hardware-ble.md](rama-hardware-ble.md) (setup del puente) y
> [resultados-calibracion.md](resultados-calibracion.md) (procedimiento).
>
> **[2026-09-09, pedido explícito del usuario]** Esta rama dejó `dwm-gui` con
> **solo estas dos pestañas** — las pestañas USB (Conexión/Terminal/Validar/
> Calibrar) generaban confusión al convivir con el flujo BLE y se sacaron de
> la ventana (el código sigue en el repo, solo dejó de estar cableado en
> `gui/main_window.py`; ver docstring de ese módulo).

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

> **[Corregido 2026-09-09, hardware real]** Esta sección describía antes un
> sampler por *polling* (`calibration/poll_sampler.py`, comando `THREAD`
> repetido) porque se creía que el puente nRF52840 no reenviaba
> notificaciones espontáneas. Esa descripción quedó **obsoleta dos veces**:
> primero se confirmó que sí las reenvía de forma pasiva (sin polling), y
> después se encontró que ese reenvío pasivo tenía un techo real de ~8 s por
> invocación de comando (ver más abajo). El firmware del puente terminó
> agregando un canal dedicado que resuelve el problema de fondo — es lo que
> usa la GUI hoy. `poll_sampler.py` sigue en el repo pero **ya no lo usa
> ningún flujo de la GUI**.

- **Canal de streaming BLE dedicado** (`transport/ble_link.py`,
  `STREAM_SERVICE_UUID`/`STREAM_DATA_CHAR_UUID`): cada nodo, al conectarse,
  se suscribe a una característica GATT separada de los comandos (`qorvo
  <cmd>`) y activa el reenvío continuo con `qorvo stream on`. Las
  notificaciones `SESSION_INFO_NTF` de la sesión de ranging llegan por ese
  canal, no por el de comandos — así un `STAT` u otro comando enviado
  mientras se rankea nunca compite por los mismos datos.
  - **Por qué hizo falta:** el canal de comandos original es
    petición/respuesta con una ventana acotada por el firmware puente
    (silencio 400 ms / timeout duro 8000 ms); con `SESSION_INFO_NTF`
    llegando cada `BLOCK` ms sin pausa durante el ranging, el silencio nunca
    se cumplía, la ventana corría siempre hasta los 8000 ms, volcaba una
    única ráfaga de `8000/BLOCK` notificaciones (~40 con `BLOCK=200`) y
    **suspendía el UART hacia el Qorvo incondicionalmente** — todo lo que se
    medía después se perdía hasta el próximo comando. Confirmado contra
    hardware real y en el código del firmware puente (`qorvo_bridge.c`,
    `QORVO_TOTAL_TIMEOUT_MS`/`uart_irq_rx_disable()`).
  - El streaming se apaga solo si la conexión BLE se cae (a diferencia del
    encendido físico del Qorvo, que persiste) — `BleTransport` lo reactiva
    automáticamente en cada reconexión (no solo al conectar la primera vez).
  - Con el streaming activo, **no hace falta ningún keepalive adicional**:
    el propio tráfico de streaming mantiene viva la conexión BLE. Un
    keepalive `STAT` periódico se probó y **resultó contraproducente**
    (reabre la ventana de 8 s del canal de comandos para la respuesta de
    ese comando específico) — no está en el código actual.
- El bucle de calibración es el mismo de siempre
  ([`calibration/autocal.py`](../src/dwm3001c_cli/calibration/autocal.py)),
  con el sampler inyectable (`_ble_sampler` en
  [`gui/workers.py`](../src/dwm3001c_cli/gui/workers.py), que llama a
  `calibration/sampler.py::collect_samples` — la misma función que usa el
  banco USB-USB): mismo respaldo previo, misma salvaguarda de corrección
  máxima, misma restauración ante error y mismo reporte en
  `reports/calibracion-*.json`.
- Los transportes BLE se abren y cierran dentro del worker de la calibración:
  si algo falla, **siempre** se cierran ambos antes de reportar el error.
- Reportes y backups se escriben en `reports/` igual que en la CLI.

## 5. Pestaña "Medir" (sin calibrar)

> **[Agregado 2026-09-09, pedido explícito del usuario]** Para cuando solo
> hace falta ver la distancia entre dos nodos (verificar montaje, comparar
> contra una calibración ya hecha, etc.) sin correr el bucle de calibración
> ni tocar `ant_delay`.

Mismo escaneo + filtro + selección de dos nodos que en "Calibración BLE"
(§2.1-§2.3), pero:

- No pide distancia real, muestras por medición, tolerancia, iteraciones ni
  `SAVE` — no hay nada que calibrar, así que no hay nada que confirmar.
- Dos botones: **"Iniciar medición"** conecta ambos nodos, arranca la sesión
  TWR (`RESPF` + `INITF`, roles INITIATOR/RESPONDER igual que en calibración)
  y empieza a leer mediciones sin límite de cantidad ni de tiempo; **"Frenar
  medición"** (habilitado recién mientras hay una medición corriendo) pide
  terminar — el pedido se atiende entre una lectura y la siguiente (bloques
  de ~0.6 s), nunca instantáneo pero sí rápido.
- Igual que "Calibración BLE": batería/versión de firmware del puente se
  muestran por nodo apenas conecta (ver §3, mismo mecanismo,
  `BleDeviceStatus`), y la etiqueta grande de distancia en vivo se actualiza
  con cada medición — acá además muestra el **desvío estándar** de la
  ventana móvil (últimas 20 mediciones exitosas), no solo la media.
- Al frenar (o si la sesión termina sola por un error), un renglón de
  "Resumen" en el log deja la media, desvío, mínimo y máximo de **toda** la
  sesión (no solo la ventana móvil de 20).
- Implementación: `BleMeasureWorker` en
  [`gui/workers.py`](../src/dwm3001c_cli/gui/workers.py) — mismo patrón de
  apertura/cierre de transportes que `BlePairCalibrationWorker` (§4), pero
  sin el bucle de ajuste de `autocalibrate`: arranca la sesión una sola vez y
  lee mediciones en bloques cortos hasta que se pide frenar. El frenado usa
  un `threading.Event` (no una señal Qt): mientras `run()` está bloqueado
  leyendo, el hilo del worker todavía no llegó al loop de eventos de Qt, así
  que una señal en cola no se entregaría a tiempo — `Event.set()` sí es
  seguro de llamar directamente desde el hilo de UI.

## 6. Limitaciones conocidas

- El rol por BLE es siempre el descrito arriba (INITIATOR = referencia,
  RESPONDER = a calibrar); no hay modo de invertirlos desde la GUI.
- La reconexión BLE del puente tras ~7-8 s de inactividad es normal (ver
  [rama-hardware-ble.md](rama-hardware-ble.md) §8); el transporte la maneja
  solo, incluida la reactivación del streaming (ver §4).
- Requiere el firmware del puente con soporte de streaming (`qorvo stream
  on|off`, ver `I-mop-nrf52840-fw/doc/00_BLE_Protocol_Specification.md`
  §5.4/§7.7). Con firmware anterior a ese cambio, la calibración por BLE
  vuelve a estar limitada a ~40 muestras por sesión (ver §4).
- Si el escaneo devuelve menos de 2 dispositivos, revisá que ambos puentes
  estén encendidos y que Windows no tenga la sesión Bluetooth ocupada por otra
  aplicación.
