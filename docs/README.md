# Documentación del proyecto — Índice

> **Propósito:** punto de entrada a toda la documentación del proyecto `dwm3001c-cli`.
> **Convención:** todos los documentos se redactan en español, en Markdown, con el mismo estilo técnico (títulos numerados, tablas, notas `> **Nota**`). Lo que no proviene de documentación oficial de Qorvo se marca **[Fuera del manual]**.

---

## 1. Documentos del proyecto

| Documento | Contenido | Audiencia |
|---|---|---|
| [../README.md](../README.md) | Presentación, instalación y uso de la herramienta | Usuarios |
| [../CLAUDE.md](../CLAUDE.md) | Contexto, reglas de programación, estructura y flujo Git | Desarrolladores / IA |
| [arquitectura.md](arquitectura.md) | Diseño del software: capas, módulos, responsabilidades y flujo de datos | Desarrolladores |
| [plan-implementacion.md](plan-implementacion.md) | Plan de implementación por fases, con especificación detallada de cada módulo y criterios de aceptación | Desarrolladores / IA implementadora |

## 2. Documentos de referencia (fabricante)

Ubicados en [referencias/](referencias/README.md), con su propio índice:

| Documento | Contenido |
|---|---|
| [referencias/guia-cli-calibracion-dwm3001cdk.md](referencias/guia-cli-calibracion-dwm3001cdk.md) | **Fuente de verdad del proyecto.** Guía verificada contra el *DWM3001CDK Developer Manual* (QM33SDK-1.1.1): comandos CLI, estados, claves de calibración y procedimiento de calibración. |
| [referencias/DWM3001CDK-quick-start-guide.pdf](referencias/DWM3001CDK-quick-start-guide.pdf) | Quick Start Guide de la placa: conectores, puesta en marcha. |
| [referencias/APS014-antenna-delay-calibration.pdf](referencias/APS014-antenna-delay-calibration.pdf) | Nota de aplicación APS014: teoría de la calibración del retardo de antena. |
| [referencias/APH301-hardware-design-guide.pdf](referencias/APH301-hardware-design-guide.pdf) | Nota de aplicación APH301: guía de diseño de hardware DW3000/QM33100. |
| [referencias/DWM3001C-esquematico-pcb.pdf](referencias/DWM3001C-esquematico-pcb.pdf) | Esquemático del módulo DWM3001C. |

## 3. Cómo agregar un documento

1. Redactarlo en español siguiendo el estilo de la guía de referencia.
2. Iniciarlo con un encabezado `>` que indique **propósito** y **alcance**.
3. Agregarlo a la tabla correspondiente de este índice en el mismo pull request.
