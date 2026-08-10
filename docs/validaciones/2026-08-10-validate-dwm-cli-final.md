# Reporte de validación de comandos CLI — DWM3001CDK

> **Placa:** COM26 · **Fecha:** 2026-08-10 10:25:48
> **Firmware:** 1.1.0 (Aug 13 2025 14:23:02) · **Dispositivo:** DWM3001CDK - DW3_QM33_SDK - FreeRTOS

**Resultado:** 18 PASS · 0 FAIL · 0 SKIP (total 18)

| Check | Estado | Duración | Detalle |
|---|---|---|---|
| A1 HELP | PASS | 0.0 s | lista de comandos con 20 líneas |
| A2 HELP INITF | PASS | 0.1 s | ayuda específica de INITF disponible |
| A3 STAT | PASS | 0.0 s | fw 1.1.0 (Aug 13 2025 14:23:02); apps ['LISTENER', 'RESPF', 'INITF'] |
| A4 THREAD | PASS | 0.0 s | información de hilos con 10 líneas |
| A5 DECAID | PASS | 0.0 s | Device ID 0xdeca0302, Part ID 0x4ef24713 |
| A6 GETOTP | PASS | 0.0 s | volcado OTP con 131 líneas |
| A7 LISTCAL | PASS | 0.2 s | 259 claves; ant0.ch9.ant_delay=0x3FFC |
| A8 CALKEY (lectura) | PASS | 0.3 s | ant0.ch9.ant_delay=0x3FFC (16380), len 4 |
| A9 UART (consulta) | PASS | 0.0 s | consulta de estado UART respondida |
| A10 DIAG (consulta) | PASS | 0.0 s | consulta de modo diagnóstico respondida |
| A11 LCFG | PASS | 0.0 s | configuración de LISTENER disponible |
| B1 DIAG (toggle) | PASS | 0.0 s | modo diagnóstico habilitado y verificado |
| B2 CALKEY (escritura neutra) | PASS | 0.7 s | restricted_channels reescrita con su propio valor (0) |
| B3 SETAPP + SAVE | PASS | 0.1 s | SETAPP NONE + SAVE confirmados (estado deseado del banco) |
| C1 LISTENER | PASS | 3.4 s | aplicación LISTENER corriendo |
| C2 INITF | PASS | 3.5 s | aplicación INITF corriendo |
| C3 RESPF | PASS | 3.5 s | aplicación RESPF corriendo |
| C4 Sesión TWR (2 placas) | PASS | 11.6 s | 50/50 mediciones SUCCESS; ejemplo: 69 cm |
