# Referencias — Documentación del fabricante

> **Propósito:** almacenar los documentos de Qorvo y la guía verificada que definen el comportamiento del firmware CLI del DWM3001C. Estos documentos **no se editan** (salvo la guía, que se corrige solo contrastando contra el Developer Manual oficial).

---

## Índice

| Archivo | Documento original | Rev./Fecha | Uso en este proyecto |
|---|---|---|---|
| `guia-cli-calibracion-dwm3001cdk.md` | Guía práctica de uso y calibración del firmware CLI (elaboración propia, verificada contra el *DWM3001CDK Developer Manual*, QM33SDK-1.1.1, ago. 2025) | ago. 2026 | **Fuente de verdad** de comandos, estados, claves de calibración y procedimiento de calibración. |
| `DWM3001CDK-quick-start-guide.pdf` | *DWM3001CDK Quick Start Guide* | Rev. B, may. 2022 | Conectores (J9/J20), alimentación, puesta en marcha. |
| `APS014-antenna-delay-calibration.pdf` | *APS014 — Antenna Delay Calibration of DW1000-Based Products and Systems* | v1.3, 2024 | Fundamento teórico del retardo de antena y su efecto en la medición de distancia. |
| `APH301-hardware-design-guide.pdf` | *APH301 — Hardware Design Guide for DW3000 and QM33100 Series ICs* | Rev. A, ene. 2024 | Contexto de hardware del transceptor. |
| `DWM3001C-esquematico-pcb.pdf` | Esquemático DWM3001C-100 (FOR-001324) | Rev. B/D, 2021 | Referencia de circuito: nRF52833, DW3000, alimentación, I2C/SPI. |

> **Nota de jerarquía documental:** ante contradicciones, el orden de precedencia es (1) *DWM3001CDK Developer Manual* oficial, (2) guía verificada, (3) notas de aplicación, (4) Quick Start Guide. La guía verificada ya registra en su Anexo A las discrepancias conocidas entre estas fuentes.

> **Nota de licencia:** los PDF son propiedad de Qorvo US, Inc. y se incluyen únicamente como referencia interna de desarrollo.
